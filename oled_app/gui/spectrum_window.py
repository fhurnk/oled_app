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
    run_spectrum_measurement,
)
from oled_app.series import ensure_measurement_folder, quarter_led_color
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import as_float_or_none, parse_float

from .progress import SpectrumProgressWindow
from .widgets import fit_toplevel_to_content


def spectrum_selection_visibility(mode: str) -> tuple[bool, bool]:
    """Return visibility of the pixel and substrate selectors for a capture mode."""

    return mode == "single", mode == "substrate"


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

    pixel_label = ttk.Label(frame, text="Пиксель:")
    pixel_label.grid(row=1, column=0, sticky="e", pady=5)
    pixel_var = tk.StringVar(value=pixels[0])
    pixel_combo = ttk.Combobox(frame, values=pixels, textvariable=pixel_var, state="readonly", width=28)
    pixel_combo.grid(row=1, column=1, sticky="w", pady=5)

    substrate_groups = group_pixels_by_substrate(app, pixels)
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
        selected = substrate_groups.get(substrate_var.get(), [])
        substrate_info_var.set(
            "Пиксели подложки, доступные для спектров: "
            + (", ".join(selected) if selected else "нет доступных пикселей")
        )

    pixel_combo.bind("<<ComboboxSelected>>", update_opening_info)
    substrate_combo.bind("<<ComboboxSelected>>", update_substrate_info)
    update_substrate_info()

    def update_selection_visibility(*_args) -> None:
        show_pixel, show_substrate = spectrum_selection_visibility(mode_var.get())
        for widget in (pixel_label, pixel_combo):
            widget.grid() if show_pixel else widget.grid_remove()
        for widget in (substrate_label, substrate_combo, substrate_info_label):
            widget.grid() if show_substrate else widget.grid_remove()
        if show_pixel:
            update_opening_info()
        else:
            selected = substrate_groups.get(substrate_var.get(), [])
            if selected:
                pixel = app.series.journal.get_pixel(selected[0]) if app.series is not None else None
                opening = as_float_or_none(pixel.get("Opening voltage (V)")) if pixel else None
                opening_info_var.set(
                    f"V открытия первого пикселя: {opening:.3f} В" if opening is not None else "V открытия первого пикселя: нет"
                )

    mode_var.trace_add("write", update_selection_visibility)
    update_selection_visibility()

    def start() -> None:
        try:
            selected_mode = mode_var.get()
            if selected_mode == "single":
                selected_pixels = [pixel_var.get()]
            else:
                selected_pixels = substrate_groups.get(substrate_var.get(), [])
            if not selected_pixels:
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
        else:
            measure_substrate_spectra(app, selected_pixels, params)

    ttk.Label(
        frame,
        text=(
            "При съёмке всей подложки V открытия подставляется отдельно для каждого пикселя. "
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
        progress.close()
        app.series.journal.update_after_measurement(
            "SPECTRUM",
            pixel_id,
            result["status"],
            result["file"],
            pixel_params.as_dict(),
            notes="Остановлено пользователем" if result.get("stopped_by_user") else "",
            spectrum_peak_count=result.get("spectrum_peak_count"),
            spectrum_peaks_nm=result.get("spectrum_peaks_nm", ""),
            spectrum_max_intensity=result.get("spectrum_max_intensity"),
        )
        app.log(f"Спектры завершены: {pixel_id}, файл {result['file'].name}")
        app.refresh_pixel_table()
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


def measure_substrate_spectra(app, pixels: List[str], params: SpectrumParams) -> None:
    measured: List[str] = []
    for pixel_id in pixels:
        choice = messagebox.askyesnocancel(
            "Спектры всей подложки",
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
        if result is not None:
            measured.append(pixel_id)
            if result.get("stopped_by_user"):
                app.log("Очередь спектров подложки остановлена вместе с текущей съёмкой.")
                break
    app.show_measurement_menu()


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
    )


def params_for_pixel(app, pixel_id: str, params: SpectrumParams) -> SpectrumParams:
    if app.series is None:
        return params
    coeff = app.series.luminance_coefficient_for_pixel(pixel_id, app.app_settings)
    led_type = params.led_type
    if not led_type or str(led_type).strip().lower() == "auto":
        row = app.series.journal.get_pixel(pixel_id) or {}
        quarter_number = int(row.get("Quarter number") or 1)
        led_type = quarter_led_color(app.series.config, quarter_number)
    return replace(params, luminance_cd_m2_per_uA=coeff, led_type=led_type)
