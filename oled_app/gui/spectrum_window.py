"""Spectrum measurement window for the modular OLED app."""

from __future__ import annotations

import tkinter as tk
import traceback
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from oled_app.hardware import effective_com_port
from oled_app.measurements.spectrum import (
    SpectrumMeasurementController,
    SpectrumParams,
    discard_spectrum_artifacts,
    run_spectrum_measurement,
    save_rejected_spectrum_workbook,
)
from oled_app.series import ensure_measurement_folder, quarter_led_color
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import as_float_or_none, parse_float

from .progress import SpectrumProgressWindow
from .widgets import fit_toplevel_to_content


def spectrum_selection_visibility(mode: str) -> tuple[bool, bool]:
    """Return visibility of the pixel and substrate selectors for a capture mode."""

    return mode in {"single", "substrate", "queue"}, mode == "substrate"


def open_spectrum_window(app) -> None:
    if app.series is None:
        return
    pixels = app.pixel_ids(require_ivl=True, require_opening=True)
    if not pixels:
        messagebox.showwarning("Спектры", "В журнале нет пикселей с ВАЯХ и заданным напряжением открытия.", parent=app)
        return

    win = tk.Toplevel(app)
    win.title("Спектры")
    win.geometry("620x650")
    win.transient(app)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    mode_var = tk.StringVar(value="single")
    ttk.Radiobutton(frame, text="Конкретный пиксель", variable=mode_var, value="single").grid(
        row=0, column=0, sticky="w", pady=4
    )
    ttk.Radiobutton(frame, text="Вся подложка последовательно", variable=mode_var, value="substrate").grid(
        row=0, column=1, sticky="w", pady=4
    )
    ttk.Radiobutton(frame, text="Очередь серии", variable=mode_var, value="queue").grid(
        row=0, column=2, sticky="w", pady=4
    )

    pixel_label = ttk.Label(frame, text="Пиксель:")
    pixel_label.grid(row=1, column=0, sticky="e", pady=5)
    pixel_var = tk.StringVar(value=pixels[0])
    pixel_combo = ttk.Combobox(frame, values=pixels, textvariable=pixel_var, state="readonly", width=28)
    pixel_combo.grid(row=1, column=1, sticky="w", pady=5)

    substrate_groups = group_pixels_by_substrate(app, pixels)
    queue_pixels = queued_spectrum_pixels(app, pixels)
    substrate_values = list(substrate_groups)
    substrate_label = ttk.Label(frame, text="Подложка:")
    substrate_label.grid(row=2, column=0, sticky="e", pady=5)
    substrate_var = tk.StringVar(value=substrate_values[0] if substrate_values else "")
    substrate_combo = ttk.Combobox(
        frame,
        values=substrate_values,
        textvariable=substrate_var,
        state="readonly",
        width=28,
    )
    substrate_combo.grid(row=2, column=1, sticky="w", pady=5)
    substrate_info_var = tk.StringVar()
    substrate_info_label = ttk.Label(frame, textvariable=substrate_info_var, foreground="#555555", wraplength=570)
    substrate_info_label.grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(0, 4)
    )
    queued_only_var = tk.BooleanVar(value=False)
    queued_only_check = ttk.Checkbutton(
        frame,
        text="Только отмеченные пиксели этой подложки",
        variable=queued_only_var,
    )
    queued_only_check.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=5)
    queue_info_label = ttk.Label(
        frame,
        text=(
            "Снимаются только пиксели с флажком в столбце «Спектры». "
            "Можно выбрать стартовый пиксель; после последнего пункта очередь завершится."
        ),
        foreground="#555555",
        wraplength=570,
    )
    queue_info_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 4))

    first_pixel = app.series.journal.get_pixel(pixels[0])
    first_opening = as_float_or_none(first_pixel.get("Opening voltage (V)")) if first_pixel else None
    saved_spectrum = app.measurement_defaults("spectrum")
    use_opening_var = tk.BooleanVar(value=bool(saved_spectrum.get("use_opening_voltage", True)))
    opening_info_var = tk.StringVar(value=f"V открытия: {first_opening:.3f} В" if first_opening is not None else "V открытия: нет")

    fields = [
        ("COM port", str(app.app_settings.get("com_port", "COM3"))),
        ("Voltage start, V", initial_spectrum_start_value(saved_spectrum, first_opening)),
        ("Voltage end, V", str(saved_spectrum.get("voltage_end_V", "5"))),
        ("Voltage step, V", str(saved_spectrum.get("voltage_step_V", "0.1"))),
        ("Current limit, mA", str(saved_spectrum.get("current_limit_mA", "6"))),
        ("LED type", str(saved_spectrum.get("led_type", "auto"))),
    ]
    vars_: Dict[str, tk.StringVar] = {}
    for row_idx, (label, default) in enumerate(fields, start=4):
        ttk.Label(frame, text=label + ":").grid(row=row_idx, column=0, sticky="e", pady=3, padx=(0, 8))
        var = tk.StringVar(value=default)
        vars_[label] = var
        ttk.Entry(frame, textvariable=var, width=18).grid(row=row_idx, column=1, sticky="w", pady=3)

    ttk.Checkbutton(frame, text="Стартовать от V открытия из журнала", variable=use_opening_var).grid(
        row=len(fields) + 4,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(8, 2),
    )
    ttk.Label(frame, textvariable=opening_info_var, foreground="#555555").grid(
        row=len(fields) + 5,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(0, 2),
    )

    def update_opening_info(_event=None) -> None:
        pixel = app.series.journal.get_pixel(pixel_var.get()) if app.series is not None else None
        opening = as_float_or_none(pixel.get("Opening voltage (V)")) if pixel else None
        opening_info_var.set(f"V открытия: {opening:.3f} В" if opening is not None else "V открытия: нет")

    def update_substrate_info(_event=None) -> None:
        selected = selected_substrate_pixels(
            app,
            substrate_groups.get(substrate_var.get(), []),
            queued_only=bool(queued_only_var.get()),
        )
        pixel_combo.configure(values=selected)
        if pixel_var.get() not in selected:
            pixel_var.set(selected[0] if selected else "")
        substrate_info_var.set(
            (
                "Отмеченные пиксели подложки: "
                if queued_only_var.get()
                else "Пиксели подложки, доступные для спектров: "
            )
            + (", ".join(selected) if selected else "нет доступных пикселей")
        )
        update_opening_info()

    pixel_combo.bind("<<ComboboxSelected>>", update_opening_info)
    substrate_combo.bind("<<ComboboxSelected>>", update_substrate_info)
    queued_only_var.trace_add("write", lambda *_args: update_substrate_info())
    update_substrate_info()

    def update_selection_visibility(*_args) -> None:
        show_pixel, show_substrate = spectrum_selection_visibility(mode_var.get())
        pixel_label.configure(
            text="Начать с пикселя:"
            if mode_var.get() in {"substrate", "queue"}
            else "Пиксель:"
        )
        for widget in (pixel_label, pixel_combo):
            widget.grid() if show_pixel else widget.grid_remove()
        for widget in (substrate_label, substrate_combo, substrate_info_label):
            widget.grid() if show_substrate else widget.grid_remove()
        queued_only_check.grid() if show_substrate else queued_only_check.grid_remove()
        queue_info_label.grid() if mode_var.get() == "queue" else queue_info_label.grid_remove()
        if mode_var.get() == "single":
            pixel_combo.configure(values=pixels)
            if pixel_var.get() not in pixels:
                pixel_var.set(pixels[0] if pixels else "")
            update_opening_info()
        elif show_substrate:
            update_substrate_info()
        elif mode_var.get() == "queue":
            pixel_combo.configure(values=queue_pixels)
            if pixel_var.get() not in queue_pixels:
                pixel_var.set(queue_pixels[0] if queue_pixels else "")
            update_opening_info()
        else:
            opening_info_var.set("V открытия подставляется отдельно для каждого пикселя")

    mode_var.trace_add("write", update_selection_visibility)
    update_selection_visibility()

    def start() -> None:
        try:
            selected_mode = mode_var.get()
            if selected_mode == "single":
                selected_pixels = [pixel_var.get()]
            elif selected_mode == "substrate":
                selected_pixels = selected_substrate_pixels(
                    app,
                    substrate_groups.get(substrate_var.get(), []),
                    queued_only=bool(queued_only_var.get()),
                )
                selected_pixels = sequence_from_start(selected_pixels, pixel_var.get())
            else:
                selected_pixels = sequence_from_start(queue_pixels, pixel_var.get())
            if not selected_pixels:
                if selected_mode == "substrate" and queued_only_var.get():
                    raise ValueError("На выбранной подложке нет отмеченных пикселей без снятых спектров")
                if selected_mode == "queue":
                    raise ValueError("Очередь спектров пуста")
                raise ValueError("Для выбранной подложки нет доступных пикселей")

            first_id = selected_pixels[0]
            pixel = app.series.journal.get_pixel(first_id)
            opening = as_float_or_none(pixel.get("Opening voltage (V)")) if pixel else None
            if opening is None:
                raise ValueError(f"Для пикселя {first_id} нет напряжения открытия")
            params = build_spectrum_params(app, vars_, opening, bool(use_opening_var.get()))
            app.save_measurement_defaults(
                "spectrum",
                {
                    "voltage_start_V": vars_["Voltage start, V"].get(),
                    "voltage_end_V": vars_["Voltage end, V"].get(),
                    "voltage_step_V": vars_["Voltage step, V"].get(),
                    "current_limit_mA": vars_["Current limit, mA"].get(),
                    "led_type": vars_["LED type"].get(),
                    "use_opening_voltage": bool(use_opening_var.get()),
                },
            )
        except Exception as exc:
            messagebox.showerror("Ошибка параметров спектров", str(exc), parent=win)
            return

        win.destroy()
        if selected_mode == "single":
            measure_one_spectrum(app, first_id, params)
        elif selected_mode == "substrate":
            measure_substrate_spectra(app, selected_pixels, params)
        else:
            measure_spectrum_queue(app, selected_pixels, params)

    ttk.Label(
        frame,
        text=(
            "Подложку можно начать с любого выбранного пикселя; более ранние пиксели будут пропущены. "
            "V открытия подставляется отдельно для каждого пикселя. "
            "При ручном старте одно введённое напряжение применяется ко всем её пикселям."
        ),
        foreground="#555555",
        wraplength=570,
    ).grid(row=len(fields) + 6, column=0, columnspan=2, sticky="w", pady=(8, 2))
    ttk.Button(frame, text="Открыть настройки", command=app.open_settings_window).grid(
        row=len(fields) + 7,
        column=0,
        sticky="w",
        pady=16,
    )
    ttk.Button(frame, text="Начать съёмку спектров", command=start).grid(
        row=len(fields) + 7,
        column=1,
        sticky="w",
        pady=16,
    )
    fit_toplevel_to_content(win, 680, 760)


def initial_spectrum_start_value(saved: Dict[str, Any], opening: Optional[float]) -> str:
    """Keep the user's last entered start voltage independent of journal opening voltage."""

    saved_value = as_float_or_none(saved.get("voltage_start_V"))
    if saved_value is not None:
        return f"{saved_value:g}"
    if opening is not None:
        return f"{float(opening):.3f}"
    return "2.0"


def group_pixels_by_substrate(app, pixels: List[str]) -> Dict[str, List[str]]:
    """Return eligible pixels grouped in physical substrate order."""

    groups: Dict[str, List[tuple[int, str]]] = {}
    for pixel_id in pixels:
        row = app.series.journal.get_pixel(pixel_id) if app.series is not None else None
        if not row:
            continue
        substrate_id = str(pixel_id).rsplit("_", 1)[0]
        try:
            pixel_number = int(row.get("Pixel number") or str(pixel_id).rsplit("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            pixel_number = 9999
        groups.setdefault(substrate_id, []).append((pixel_number, pixel_id))
    return {
        substrate_id: [pixel_id for _number, pixel_id in sorted(items)]
        for substrate_id, items in groups.items()
    }


def selected_substrate_pixels(app, pixels: List[str], queued_only: bool) -> List[str]:
    if not queued_only or app.series is None:
        return list(pixels)
    return [
        pixel_id
        for pixel_id in pixels
        if bool((app.series.journal.get_pixel(pixel_id) or {}).get("Spectrum priority"))
    ]


def queued_spectrum_pixels(app, pixels: List[str]) -> List[str]:
    """Return only explicitly queued pixels that do not have a saved spectrum."""

    if app.series is None:
        return []
    result: List[str] = []
    for pixel_id in pixels:
        row = app.series.journal.get_pixel(pixel_id) or {}
        if bool(row.get("Spectrum priority")) and not row.get("Last spectrum file"):
            result.append(pixel_id)
    return result


def sequence_from_start(pixels: List[str], start_pixel: str) -> List[str]:
    """Match the IVL-series behavior: begin at the selected item without wrapping."""

    if start_pixel not in pixels:
        return list(pixels)
    return list(pixels[pixels.index(start_pixel) :])


def params_for_pixel_opening(app, pixel_id: str, params: SpectrumParams) -> SpectrumParams:
    row = app.series.journal.get_pixel(pixel_id) if app.series is not None else None
    opening = as_float_or_none(row.get("Opening voltage (V)")) if row else None
    if opening is None:
        raise ValueError(f"Для пикселя {pixel_id} нет напряжения открытия")
    pixel_params = replace(params, opening_voltage=float(opening))
    if params.voltage_start_source == "opening":
        pixel_params = replace(pixel_params, voltage_start=float(opening))
    return params_for_pixel(app, pixel_id, pixel_params)


def measure_one_spectrum(
    app,
    pixel_id: str,
    params: SpectrumParams,
    return_to_menu: bool = True,
) -> Optional[Dict[str, Any]]:
    assert app.series is not None
    progress = None
    controller = SpectrumMeasurementController()
    try:
        pixel_params = params_for_pixel_opening(app, pixel_id, params)
        output_dir = ensure_measurement_folder(
            app.series.series_folder,
            "SPECTRUM",
            pixel_id,
            app.series.journal.get_pixel(pixel_id),
        )
        progress = SpectrumProgressWindow(app, pixel_id, controller)
        result = run_spectrum_measurement(
            pixel_id,
            output_dir,
            pixel_params,
            app.log,
            app.app_settings,
            progress_callback=progress.update_spectrum,
            optimization_preview_callback=progress.update_optimization_preview,
            control=controller,
        )
        rejected_data_note = ""
        if result.get("discarded"):
            rejected_data_note = handle_rejected_spectrum_data(
                app,
                pixel_id,
                pixel_params,
                result,
            )
        progress.close()
        app.series.journal.update_after_measurement(
            "SPECTRUM",
            pixel_id,
            result["status"],
            result["file"],
            pixel_params.as_dict(),
            notes=(
                "Остановлено пользователем"
                if result.get("stopped_by_user")
                else rejected_data_note
            ),
            spectrum_peak_count=result.get("spectrum_peak_count"),
            spectrum_peaks_nm=result.get("spectrum_peaks_nm", ""),
            spectrum_max_intensity=result.get("spectrum_max_intensity"),
        )
        if result.get("discarded") and result.get("file") is not None:
            app.log(
                f"Невалидный спектр {pixel_id} сохранён по выбору пользователя: "
                f"{result['file'].name}"
            )
        elif result.get("file") is not None:
            app.log(f"Спектры завершены: {pixel_id}, файл {result['file'].name}")
        else:
            app.log(
                f"Спектр {pixel_id} не сохранён: {result.get('status', 'FAILED')}."
            )
        app.refresh_pixel_table()
        if (
            return_to_menu
            and result.get("discarded")
            and result.get("status") == "NO_CONTACT"
            and ask_no_contact_retry(app, pixel_id)
        ):
            app.log(
                f"{pixel_id}: повторная съёмка спектра после проверки контакта."
            )
            return measure_one_spectrum(
                app,
                pixel_id,
                params,
                return_to_menu=True,
            )
        if return_to_menu:
            app.show_measurement_menu()
        return result
    except Exception as exc:
        if progress is not None:
            progress.close()
        app.log(traceback.format_exc())
        messagebox.showerror("Ошибка спектров", str(exc), parent=app)
        if return_to_menu:
            app.show_measurement_menu()
        return None


def handle_rejected_spectrum_data(
    app,
    pixel_id: str,
    params: SpectrumParams,
    result: Dict[str, Any],
) -> str:
    """Route no-contact attempts to retry and other rejections to save/delete."""

    if str(result.get("status") or "").upper() != "NO_CONTACT":
        return resolve_rejected_spectrum_data(app, pixel_id, params, result)

    raw_files = [path for path in result.get("raw_files", []) if path]
    discard_spectrum_artifacts(raw_files, app.log)
    result["raw_files"] = []
    result["rejected_data_kept"] = False
    app.log(
        f"{pixel_id}: данные попытки без контакта удалены; требуется переставить "
        "пиксель и повторить съёмку."
    )
    return "NO_CONTACT: частичные данные удалены, требуется повторная съёмка"


def ask_no_contact_retry(app, pixel_id: str) -> bool:
    """Show the IVL-style contact warning and offer the same pixel again."""

    return bool(
        messagebox.askretrycancel(
            "Нет контакта",
            (
                f"Пиксель {pixel_id}: ток почти нулевой — контакта нет.\n\n"
                "Переставьте или проверьте контакт этого пикселя, затем нажмите "
                "«Повторить», чтобы заново снять тот же пиксель."
            ),
            icon="warning",
            parent=app,
        )
    )


def resolve_rejected_spectrum_data(
    app,
    pixel_id: str,
    params: SpectrumParams,
    result: Dict[str, Any],
) -> str:
    """Ask whether partial raw CSV from an electrically rejected pixel should remain."""

    if str(result.get("status") or "").upper() == "NO_CONTACT":
        return handle_rejected_spectrum_data(app, pixel_id, params, result)
    raw_files = [path for path in result.get("raw_files", []) if path]
    if not raw_files:
        return "Спектр не сохранён; частичных данных нет"
    delete_data = messagebox.askyesno(
        "Данные несохранённого спектра",
        (
            f"Спектр {pixel_id} не сохранён: {result.get('status', 'FAILED')}.\n\n"
            "Удалить частичные raw CSV этого измерения?\n\n"
            "Да — удалить данные.\n"
            "Нет — оставить CSV и создать диагностический XLSX."
        ),
        icon="warning",
        parent=app,
    )
    if delete_data:
        discard_spectrum_artifacts(raw_files, app.log)
        result["raw_files"] = []
        result["rejected_data_kept"] = False
        app.log(f"{pixel_id}: частичные данные несохранённого спектра удалены.")
        return "Спектр не сохранён; частичные raw CSV удалены пользователем"

    result["rejected_data_kept"] = True
    try:
        diagnostic_file = save_rejected_spectrum_workbook(
            pixel_id,
            params,
            result,
        )
    except Exception as exc:
        paths = ", ".join(str(path) for path in raw_files)
        app.log(
            f"{pixel_id}: raw CSV оставлены, но диагностический XLSX создать "
            f"не удалось: {exc}. Файлы: {paths}"
        )
        messagebox.showerror(
            "Ошибка сохранения диагностического XLSX",
            (
                f"Частичные CSV оставлены, но XLSX создать не удалось:\n{exc}"
            ),
            parent=app,
        )
        return f"Спектр не сохранён; raw CSV оставлены, ошибка XLSX: {exc}"

    result["file"] = diagnostic_file
    app.log(
        f"{pixel_id}: частичные данные и диагностический XLSX оставлены: "
        f"{diagnostic_file}"
    )
    return (
        "Невалидный спектр сохранён как диагностический XLSX по выбору пользователя: "
        f"{diagnostic_file}"
    )


def measure_substrate_spectra(app, pixels: List[str], params: SpectrumParams) -> None:
    measure_spectrum_sequence(app, pixels, params, title="Спектры всей подложки")


def measure_spectrum_queue(app, pixels: List[str], params: SpectrumParams) -> None:
    measure_spectrum_sequence(app, pixels, params, title="Очередь спектров серии")


def measure_spectrum_sequence(
    app,
    pixels: List[str],
    params: SpectrumParams,
    title: str,
) -> None:
    measured: List[str] = []
    attempted: List[str] = []
    remaining = list(pixels)
    while remaining:
        pixel_id = remaining.pop(0)
        choice = messagebox.askyesnocancel(
            title,
            f"Следующий пиксель: {pixel_id}\n\n"
            f"Измерены: {', '.join(measured) if measured else 'пока нет'}\n\n"
            "Да — снять следующий.\nНет — пропустить этот пиксель.\nОтмена — остановить подложку.",
            parent=app,
        )
        if choice is None:
            app.log("Съёмка спектров всей подложки остановлена пользователем.")
            break
        if choice is False:
            app.log(f"Спектры {pixel_id} пропущены пользователем.")
            continue
        result = measure_one_spectrum(app, pixel_id, params, return_to_menu=False)
        attempted.append(pixel_id)
        if result is not None:
            if result.get("stopped_by_user"):
                app.log("Очередь спектров подложки остановлена вместе с текущей съёмкой.")
                break
            if result.get("discarded"):
                if result.get("status") == "NO_CONTACT":
                    if ask_no_contact_retry(app, pixel_id):
                        remaining.insert(0, pixel_id)
                        app.log(
                            f"{pixel_id}: повторная съёмка после перестановки/"
                            "проверки контакта."
                        )
                    else:
                        app.log(
                            f"{pixel_id}: повторная съёмка после NO_CONTACT отменена."
                        )
                    continue
                if result.get("file") is not None:
                    measured.append(pixel_id)
                replacement_candidates = replacement_pixels_in_quarter(
                    app,
                    pixel_id,
                    excluded=set(attempted) | set(measured),
                )
                if replacement_candidates:
                    from .ivl_window import ask_pixel

                    replacement = ask_pixel(
                        app,
                        f"Выберите замену в четверти для {pixel_id}",
                        values=replacement_candidates,
                    )
                    if replacement:
                        if replacement in remaining:
                            remaining.remove(replacement)
                        remaining.insert(0, replacement)
                        app.log(
                            f"{pixel_id}: выбран новый пиксель той же четверти — "
                            f"{replacement}."
                        )
                continue
            if result.get("file") is not None:
                measured.append(pixel_id)
                continue
    app.show_measurement_menu()


def replacement_pixels_in_quarter(
    app,
    failed_pixel_id: str,
    excluded: set[str] | None = None,
) -> List[str]:
    """List unmeasured IVL pixels in the failed pixel's physical quarter."""

    if app.series is None:
        return []
    failed_row = app.series.journal.get_pixel(failed_pixel_id) or {}
    quarter_number = failed_row.get("Quarter number")
    excluded_ids = set(excluded or ())
    excluded_ids.add(failed_pixel_id)
    result: List[tuple[int, int, str]] = []
    for index, row in enumerate(app.series.journal.list_pixels()):
        pixel_id = str(row.get("Pixel ID") or "")
        if (
            not pixel_id
            or pixel_id in excluded_ids
            or row.get("Quarter number") != quarter_number
            or not row.get("Last IVL file")
            or as_float_or_none(row.get("Opening voltage (V)")) is None
            or row.get("Last spectrum file")
        ):
            continue
        result.append(
            (
                0 if bool(row.get("Spectrum priority")) else 1,
                index,
                pixel_id,
            )
        )
    return [pixel_id for _priority, _index, pixel_id in sorted(result)]


def build_spectrum_params(app, vars_: Dict[str, tk.StringVar], opening: float, use_opening: bool) -> SpectrumParams:
    voltage_start = float(opening) if use_opening else parse_float(vars_["Voltage start, V"].get(), "Voltage start")
    adv = app.app_settings.get("spectrum_advanced", DEFAULT_APP_SETTINGS["spectrum_advanced"])
    units = app.app_settings.get("measurement_units", DEFAULT_APP_SETTINGS["measurement_units"])
    return SpectrumParams(
        com_port=effective_com_port({**app.app_settings, "com_port": vars_["COM port"].get().strip()}, app.log),
        voltage_start=voltage_start,
        voltage_end=parse_float(vars_["Voltage end, V"].get(), "Voltage end"),
        voltage_step=parse_float(vars_["Voltage step, V"].get(), "Voltage step"),
        opening_voltage=float(opening),
        voltage_start_source="opening" if use_opening else "manual",
        current_limit_mA=parse_float(vars_["Current limit, mA"].get(), "Current limit"),
        photodiode_bias_V=float(adv.get("photodiode_bias_V", -5.0)),
        photodiode_range=int(adv.get("photodiode_range", 4)),
        target_intensity=float(adv.get("target_intensity", 40000.0)),
        intensity_min=float(adv.get("intensity_min", 20000.0)),
        intensity_max=float(adv.get("intensity_max", 55000.0)),
        saturation_level=float(adv.get("saturation_level", 60000.0)),
        min_peak_width_nm=float(adv.get("min_peak_width_nm", 15.0)),
        t_int_initial_s=float(adv.get("t_int_initial_s", 0.01)),
        t_int_min_s=float(adv.get("t_int_min_s", 0.001)),
        t_int_max_s=float(adv.get("t_int_max_s", 10.0)),
        reuse_previous_integration_time=bool(adv.get("reuse_previous_integration_time", True)),
        discard_first_scan_after_tint_change=bool(adv.get("discard_first_scan_after_tint_change", True)),
        kp=float(adv.get("kp", 0.3)),
        ki=float(adv.get("ki", 0.05)),
        max_iterations=int(adv.get("max_iterations", 20)),
        tolerance=float(adv.get("tolerance", 0.05)),
        led_type=vars_["LED type"].get().strip() or "auto",
        peak_search_mode_for_tint=str(adv.get("peak_search_mode_for_tint", "auto")),
        settle_time_voltage_s=float(adv.get("settle_time_voltage_s", 0.1)),
        settle_time_spectrum_s=float(adv.get("settle_time_spectrum_s", 0.05)),
        dark_spectrum_enabled=bool(adv.get("dark_spectrum_enabled", False)),
        dark_spectrum_scans=int(adv.get("dark_spectrum_scans", 3)),
        baseline_correction_enabled=bool(adv.get("baseline_correction_enabled", True)),
        peak_detection_enabled=bool(adv.get("peak_detection_enabled", False)),
        pixel_area_mm2=float(units.get("pixel_area_mm2", 1.0)),
        luminance_cd_m2_per_uA=float(units.get("luminance_cd_m2_per_uA", 1.0)),
        geometric_coefficient=float(units.get("geometric_conversion_coefficient", 1.0)),
    )


def params_for_pixel(app, pixel_id: str, params: SpectrumParams) -> SpectrumParams:
    if app.series is None:
        return params
    model_resolver = getattr(app.series, "luminance_model_for_pixel", None)
    model = (
        model_resolver(pixel_id, app.app_settings)
        if callable(model_resolver)
        else None
    )
    rgb_resolver = getattr(app.series, "rgb_luminance_coefficient_for_pixel", None)
    coeff = (
        rgb_resolver(pixel_id, app.app_settings)
        if model is not None and callable(rgb_resolver)
        else app.series.luminance_coefficient_for_pixel(pixel_id, app.app_settings)
    )
    led_type = params.led_type
    if not led_type or str(led_type).strip().lower() == "auto":
        row = app.series.journal.get_pixel(pixel_id) or {}
        quarter_number = int(row.get("Quarter number") or 1)
        led_type = quarter_led_color(app.series.config, quarter_number)
    geometry_resolver = getattr(app.series, "geometric_coefficient", None)
    if callable(geometry_resolver):
        geometric_coefficient = geometry_resolver(app.app_settings)
    else:
        units = app.app_settings.get("measurement_units", {})
        geometric_coefficient = float(units.get("geometric_conversion_coefficient", 1.0))
    return replace(
        params,
        luminance_cd_m2_per_uA=coeff,
        led_type=led_type,
        geometric_coefficient=geometric_coefficient,
        luminance_calibration_model=model,
    )
