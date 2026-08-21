"""IVL / ВАЯХ window and GUI orchestration for the modular OLED app."""

from __future__ import annotations

import tkinter as tk
import traceback
from dataclasses import replace
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional

from oled_app.hardware import effective_com_port
from oled_app.measurements.ivl import IVLParams, MeasurementStopped, run_ivl_measurement
from oled_app.series import ensure_measurement_folder
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import parse_float, parse_int

from .progress import IVLProgressWindow
from .widgets import fit_toplevel_to_content


def open_ivl_window(
    app,
    initial_pixel: Optional[str] = None,
    parent=None,
    locked_pixel: bool = False,
    measurement_runner=None,
) -> None:
    if app.series is None:
        return
    owner = parent or app
    win = tk.Toplevel(owner)
    win.title("ВАЯХ")
    win.geometry("560x520")
    win.transient(owner)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    pixel_values = app.pixel_ids()
    selected_pixel = initial_pixel if initial_pixel in pixel_values else (pixel_values[0] if pixel_values else "")
    pixel_var = tk.StringVar(value=selected_pixel)
    mode_var = tk.StringVar(value="single")
    saved_ivl = app.measurement_defaults("ivl")
    skip_nonworking_var = tk.BooleanVar(
        value=bool(saved_ivl.get("skip_nonworking_pixels", False))
    )
    field_start_row = 2
    if locked_pixel:
        ttk.Label(frame, text="Пиксель камеры:").grid(row=0, column=0, sticky="e", pady=5)
        ttk.Label(frame, text=selected_pixel, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=1, sticky="w", pady=5
        )
    else:
        ttk.Radiobutton(frame, text="Конкретный пиксель", variable=mode_var, value="single").grid(
            row=0, column=0, sticky="w", pady=4
        )
        ttk.Radiobutton(frame, text="Вся серия последовательно", variable=mode_var, value="series").grid(
            row=0, column=1, sticky="w", pady=4
        )
        ttk.Label(frame, text="Пиксель:").grid(row=1, column=0, sticky="e", pady=5)
        pixel_combo = ttk.Combobox(frame, textvariable=pixel_var, values=pixel_values, width=24, state="readonly")
        pixel_combo.grid(row=1, column=1, sticky="w", pady=5)
        skip_nonworking_check = ttk.Checkbutton(
            frame,
            text="Пропускать пиксели со статусом NONWORKING или BURNED",
            variable=skip_nonworking_var,
        )
        skip_nonworking_check.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 7),
        )

        def update_skip_nonworking_state(*_args) -> None:
            skip_nonworking_check.configure(
                state="normal" if mode_var.get() == "series" else "disabled"
            )

        mode_var.trace_add("write", update_skip_nonworking_state)
        update_skip_nonworking_state()
        field_start_row = 3

    fields = [
        ("Sweep start, V", str(saved_ivl.get("sweep_start_V", "0"))),
        ("Sweep end, V", str(saved_ivl.get("sweep_end_V", "5"))),
        ("Step, V", str(saved_ivl.get("step_V", "0.02"))),
        ("Time per point, s", str(saved_ivl.get("time_per_point_s", "0.01"))),
        ("Cycles", str(saved_ivl.get("cycles", "1"))),
        ("Delay between cycles, s", str(saved_ivl.get("delay_between_cycles_s", "1"))),
        ("Current limit, mA", str(saved_ivl.get("current_limit_mA", "10"))),
    ]
    vars_: Dict[str, tk.StringVar] = {}
    for row_idx, (label, default) in enumerate(fields, start=field_start_row):
        ttk.Label(frame, text=label + ":").grid(row=row_idx, column=0, sticky="e", pady=3, padx=(0, 8))
        var = tk.StringVar(value=default)
        vars_[label] = var
        ttk.Entry(frame, textvariable=var, width=18).grid(row=row_idx, column=1, sticky="w", pady=3)

    def start() -> None:
        try:
            params = build_ivl_params(app, vars_)
            app.save_measurement_defaults("ivl", {
                "sweep_start_V": vars_["Sweep start, V"].get(),
                "sweep_end_V": vars_["Sweep end, V"].get(),
                "step_V": vars_["Step, V"].get(),
                "time_per_point_s": vars_["Time per point, s"].get(),
                "cycles": vars_["Cycles"].get(),
                "delay_between_cycles_s": vars_["Delay between cycles, s"].get(),
                "current_limit_mA": vars_["Current limit, mA"].get(),
                "skip_nonworking_pixels": bool(skip_nonworking_var.get()),
            })
            selected_pixel = pixel_var.get()
            if measurement_runner is not None:
                win.destroy()
                measurement_runner(
                    "ivl",
                    selected_pixel,
                    lambda: measure_one_ivl(app, selected_pixel, params, return_to_menu=False),
                )
            elif mode_var.get() == "single":
                win.destroy()
                measure_one_ivl(app, selected_pixel, params)
            else:
                win.destroy()
                measure_series_ivl(
                    app,
                    params,
                    start_pixel=selected_pixel,
                    skip_nonworking=bool(skip_nonworking_var.get()),
                )
        except Exception as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=win)

    footer_row = field_start_row + len(fields)
    ttk.Label(
        frame,
        text="Дополнительные параметры ВАЯХ вынесены в Настройки -> ВАЯХ доп.",
        foreground="#555555",
    ).grid(row=footer_row, column=0, columnspan=2, sticky="w", pady=(10, 2))
    ttk.Button(frame, text="Открыть настройки", command=app.open_settings_window).grid(row=footer_row + 1, column=0, sticky="w", pady=12)
    ttk.Button(frame, text="Начать ВАЯХ", command=start).grid(row=footer_row + 1, column=1, sticky="w", pady=12)
    fit_toplevel_to_content(win, 620, 620)


def build_ivl_params(app, vars_: Dict[str, tk.StringVar]) -> IVLParams:
    adv = app.app_settings.get("ivl_advanced", DEFAULT_APP_SETTINGS["ivl_advanced"])
    units = app.app_settings.get("measurement_units", DEFAULT_APP_SETTINGS["measurement_units"])
    opening_threshold = float(adv.get("opening_photodiode_threshold_uA", 0.5))
    opening_confirmation_points = int(adv.get("opening_confirmation_points", 5))
    working_threshold = float(adv.get("photodiode_threshold_uA", 0.5))
    working_confirmation_points = int(adv.get("working_confirmation_points", 5))
    if working_threshold < 0:
        raise ValueError("Порог рабочего фототока не может быть отрицательным.")
    if not 1 <= working_confirmation_points <= 100:
        raise ValueError(
            "Количество следующих точек для статуса WORKING должно быть от 1 до 100."
        )
    if opening_threshold < 0:
        raise ValueError("Порог открытия по фототоку не может быть отрицательным.")
    if not 1 <= opening_confirmation_points <= 100:
        raise ValueError(
            "Количество следующих точек для подтверждения открытия "
            "должно быть от 1 до 100."
        )
    return IVLParams(
        com_port=effective_com_port(app.app_settings, app.log),
        sweep_start=parse_float(vars_["Sweep start, V"].get(), "Sweep start"),
        sweep_end=parse_float(vars_["Sweep end, V"].get(), "Sweep end"),
        sweep_increment=parse_float(vars_["Step, V"].get(), "Step"),
        sweep_time_per_point=parse_float(vars_["Time per point, s"].get(), "Time per point"),
        num_cycles=parse_int(vars_["Cycles"].get(), "Cycles"),
        delay_between_cycles=parse_float(vars_["Delay between cycles, s"].get(), "Delay"),
        current_limit_mA=parse_float(vars_["Current limit, mA"].get(), "Current limit"),
        photodiode_bias_V=float(adv.get("photodiode_bias_V", -5.0)),
        photodiode_range=int(adv.get("photodiode_range", 4)),
        photodiode_threshold_uA=working_threshold,
        working_confirmation_points=working_confirmation_points,
        opening_photodiode_threshold_uA=opening_threshold,
        opening_confirmation_points=opening_confirmation_points,
        burnout_current_threshold_mA=float(adv.get("burnout_current_threshold_mA", 10.0)),
        mark_current_limit_as_burnout=bool(adv.get("mark_current_limit_as_burnout", False)),
        no_contact_max_led_current_mA=float(adv.get("no_contact_max_led_current_mA", 0.05)),
        burned_confirmation_cycles=int(adv.get("burned_confirmation_cycles", 1)),
        pixel_area_mm2=float(units.get("pixel_area_mm2", 1.0)),
        luminance_cd_m2_per_uA=float(units.get("luminance_cd_m2_per_uA", 1.0)),
        geometric_coefficient=float(units.get("geometric_conversion_coefficient", 1.0)),
    )


def measure_one_ivl(app, pixel_id: str, params: IVLParams, return_to_menu: bool = True) -> Optional[Dict[str, Any]]:
    assert app.series is not None
    if not pixel_id:
        messagebox.showwarning("Пиксель", "Пиксель не выбран", parent=app)
        return None

    pixel_params = params_for_pixel_luminance(app, pixel_id, params)
    output_dir = ensure_measurement_folder(
        app.series.series_folder,
        "IVL",
        pixel_id,
        app.series.journal.get_pixel(pixel_id),
    )
    progress = IVLProgressWindow(app, pixel_id, pixel_params)
    measurement_session = app.begin_measurement_session("IVL", pixel_id)
    try:
        progress.set_status(f"Пиксель {pixel_id}: идет съемка ВАЯХ")
        result = run_ivl_measurement(
            pixel_id,
            output_dir,
            pixel_params,
            app.log,
            app.app_settings,
            progress_callback=progress.add_point,
            measurement_started_monotonic=measurement_session["started_monotonic"],
        )
        measurement_session["events"] = list(result.get("events") or [])
        progress.set_status(f"Пиксель {pixel_id}: измерение завершено, статус {result.get('status', '')}")
        opening = result.get("opening_voltage")
        if result["status"] == "WORKING" and opening is None:
            opening = simpledialog.askfloat(
                "Напряжение открытия",
                (
                    f"Пиксель {pixel_id}\n"
                    "Устойчивое превышение порога фототока не найдено. "
                    "Задайте напряжение открытия вручную."
                ),
                parent=app,
            )
        elif opening is not None:
            app.log(
                f"Напряжение открытия {pixel_id}: {float(opening):.3f} В "
                f"(порог ФД {pixel_params.opening_photodiode_threshold_uA:g} мкА, "
                f"следующих точек: {pixel_params.opening_confirmation_points})."
            )
        app.series.journal.update_after_measurement(
            "IVL",
            pixel_id,
            result["status"],
            result["file"],
            pixel_params.as_dict(),
            notes=result.get("ivl_diagnosis", ""),
            opening_voltage=opening,
            max_current_mA=result.get("max_current_mA"),
            max_photo_uA=result.get("max_photo_uA"),
        )
        if result.get("ivl_diagnosis"):
            app.log(f"Первый промер {pixel_id}: {result['ivl_diagnosis']}")
        if result["status"] == "NO_CONTACT":
            app.log(f"Нет контакта на {pixel_id}: переставьте/проверьте эту подложку и снимите пиксель заново.")
            messagebox.showwarning(
                "Нет контакта",
                f"Пиксель {pixel_id}: ток почти нулевой, фототока нет.\n\n"
                "Нужно переставить или проверить эту подложку и заново снять измерение. "
                "Если проблема у всей подложки, при съемке серии можно перейти к следующей подложке.",
                parent=app,
            )
        app.log(f"ВАЯХ завершена: {pixel_id}, файл {result['file'].name}")
        app.refresh_pixel_table()
        if return_to_menu:
            app.show_measurement_menu()
        progress.close()
        return result
    except MeasurementStopped as exc:
        app.log(str(exc))
        try:
            progress.set_status("Измерение остановлено. На SMU отправлено 0 В.")
        except Exception:
            pass
        messagebox.showinfo("Измерение остановлено", "Измерение остановлено. На каналы SMU отправлено 0 В.", parent=app)
        progress.close()
        if return_to_menu:
            app.show_measurement_menu()
        return None
    except Exception as exc:
        app.log(traceback.format_exc())
        try:
            progress.set_status(f"Ошибка: {exc}")
        except Exception:
            pass
        progress.close()
        messagebox.showerror("Ошибка ВАЯХ", str(exc), parent=app)
        return None
    finally:
        app.end_measurement_session(measurement_session)


def params_for_pixel_luminance(app, pixel_id: str, params: IVLParams) -> IVLParams:
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
    return replace(
        params,
        luminance_cd_m2_per_uA=coeff,
        luminance_calibration_model=model,
    )


def pixel_info_from_journal(app, pixel_id: str) -> Optional[Dict[str, Any]]:
    if app.series is None:
        return None
    return app.series.journal.get_pixel(pixel_id)


def remove_same_substrate_from_queue(app, remaining: List[str], pixel_id: str) -> List[str]:
    row = pixel_info_from_journal(app, pixel_id)
    if not row:
        return remaining
    quarter_number = row.get("Quarter number")
    substrate_number = row.get("Substrate number")
    result = []
    for queued_pixel in remaining:
        queued_row = pixel_info_from_journal(app, queued_pixel)
        if queued_row and queued_row.get("Quarter number") == quarter_number and queued_row.get("Substrate number") == substrate_number:
            continue
        result.append(queued_pixel)
    return result


def is_known_unusable_pixel(app, pixel_id: str) -> bool:
    row = pixel_info_from_journal(app, pixel_id) or {}
    return str(row.get("Last status") or "").upper() in {"NONWORKING", "BURNED"}


def ivl_series_sequence_from_pixel(
    app,
    all_pixels: List[str],
    start_pixel: Optional[str],
    measured: Optional[List[str]] = None,
    skip_nonworking: bool = False,
    include_start: bool = True,
) -> List[str]:
    start_idx = all_pixels.index(start_pixel) if start_pixel in all_pixels else 0
    if not include_start and start_pixel in all_pixels:
        start_idx += 1
    measured_set = set(measured or [])
    return [
        pixel_id
        for pixel_id in all_pixels[start_idx:]
        if pixel_id not in measured_set
        and (not skip_nonworking or not is_known_unusable_pixel(app, pixel_id))
    ]


def measure_series_ivl(
    app,
    params: IVLParams,
    start_pixel: Optional[str] = None,
    skip_nonworking: bool = False,
) -> None:
    assert app.series is not None
    all_pixels = app.pixel_ids()
    measured: List[str] = []
    remaining = ivl_series_sequence_from_pixel(
        app,
        all_pixels,
        start_pixel,
        skip_nonworking=skip_nonworking,
    )
    if skip_nonworking:
        start_idx = all_pixels.index(start_pixel) if start_pixel in all_pixels else 0
        skipped_count = sum(
            1
            for pixel_id in all_pixels[start_idx:]
            if is_known_unusable_pixel(app, pixel_id)
        )
        app.log(
            "Автопропуск NONWORKING/BURNED включен: "
            f"исключено из очереди {skipped_count} пикселей."
        )

    while remaining:
        next_pixel = remaining[0]
        message = f"Следующий пиксель: {next_pixel}\n\nИзмерены:\n" + (", ".join(measured[-20:]) if measured else "пока нет")
        choice = messagebox.askyesnocancel(
            "Съем всей серии",
            message + "\n\nДа - снять следующий.\nНет - выбрать произвольный пиксель.\nОтмена - остановить серию.",
            parent=app,
        )
        if choice is None:
            app.log("Съем всей серии отменен пользователем.")
            break
        if choice is False:
            selectable_pixels = [
                pixel_id
                for pixel_id in all_pixels
                if not skip_nonworking or not is_known_unusable_pixel(app, pixel_id)
            ]
            chosen = ask_pixel(app, "Выберите произвольный пиксель", values=selectable_pixels)
            if not chosen:
                continue
            next_pixel = chosen
            remaining = ivl_series_sequence_from_pixel(
                app,
                all_pixels,
                chosen,
                measured=measured,
                skip_nonworking=skip_nonworking,
                include_start=False,
            )
            app.log(f"Очередь ВАЯХ продолжена от выбранного пикселя {chosen}.")
        else:
            remaining.pop(0)

        while True:
            result = measure_one_ivl(app, next_pixel, params, return_to_menu=False)
            if result is None:
                break
            status = result.get("status")
            if status != "NO_CONTACT":
                if next_pixel not in measured:
                    measured.append(next_pixel)
                break

            action = messagebox.askyesnocancel(
                "Нет контакта на подложке",
                f"Для {next_pixel} нет контакта.\n\n"
                "Да - переставить/проверить эту подложку и переснять этот же пиксель.\n"
                "Нет - пропустить оставшиеся пиксели этой подложки и перейти к следующей подложке.\n"
                "Отмена - продолжить обычную очередь без пропуска.",
                parent=app,
            )
            if action is True:
                app.log(f"Повторная съемка {next_pixel} после перестановки/проверки подложки.")
                continue
            if action is False:
                remaining = remove_same_substrate_from_queue(app, remaining, next_pixel)
                app.log(f"Оставшиеся пиксели подложки {next_pixel} пропущены; переход к следующей подложке.")
            if next_pixel not in measured:
                measured.append(next_pixel)
            break

    app.show_measurement_menu()


def ask_pixel(app, title: str, values: List[str]) -> Optional[str]:
    dialog = tk.Toplevel(app)
    dialog.title(title)
    dialog.geometry("360x120")
    dialog.transient(app)
    dialog.grab_set()
    var = tk.StringVar(value=values[0] if values else "")
    ttk.Label(dialog, text=title).pack(padx=12, pady=(12, 4))
    ttk.Combobox(dialog, values=values, textvariable=var, state="readonly", width=30).pack(padx=12, pady=4)
    result: Dict[str, Optional[str]] = {"value": None}

    def ok() -> None:
        result["value"] = var.get()
        dialog.destroy()

    ttk.Button(dialog, text="OK", command=ok).pack(pady=8)
    fit_toplevel_to_content(dialog, 420, 160)
    app.wait_window(dialog)
    return result["value"]
