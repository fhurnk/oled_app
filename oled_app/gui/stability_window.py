"""Stability measurement window for the modular OLED app."""

from __future__ import annotations

import tkinter as tk
import traceback
from dataclasses import replace
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Dict, Optional

from oled_app.hardware import effective_com_port
from oled_app.measurements.stability import (
    StabilityParams,
    StabilitySetpointController,
    interpolate_voltage_at_current_from_ivl,
    run_stability_measurement,
)
from oled_app.series import ensure_measurement_folder
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import parse_float

from .progress import StabilityProgressWindow
from .widgets import fit_toplevel_to_content


MODE_LABELS = {
    "current": "Удерживать силу тока",
    "voltage": "Удерживать напряжение",
}


def open_stability_window(
    app,
    initial_pixel: Optional[str] = None,
    parent=None,
    locked_pixel: bool = False,
    measurement_runner=None,
) -> None:
    if app.series is None:
        return
    pixels = app.pixel_ids()
    if not pixels:
        messagebox.showwarning("Стабильность", "В журнале серии нет пикселей.", parent=app)
        return

    owner = parent or app
    win = tk.Toplevel(owner)
    win.title("Стабильность по току или напряжению")
    win.geometry("660x650")
    win.transient(owner)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    selected_pixel = initial_pixel if initial_pixel in pixels else pixels[0]
    pixel_var = tk.StringVar(value=selected_pixel)
    ttk.Label(frame, text="Пиксель камеры:" if locked_pixel else "Пиксель:").grid(
        row=0, column=0, sticky="e", pady=5
    )
    if locked_pixel:
        ttk.Label(frame, text=selected_pixel, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=1, sticky="w", pady=5
        )
    else:
        ttk.Combobox(frame, values=pixels, textvariable=pixel_var, state="readonly", width=28).grid(
            row=0, column=1, sticky="w", pady=5
        )

    saved = app.measurement_defaults("stability")
    mode_var = tk.StringVar(value=str(saved.get("control_mode", "current")))
    mode_box = ttk.LabelFrame(frame, text="Режим управления", padding=8)
    mode_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
    for column, (value, label) in enumerate(MODE_LABELS.items()):
        ttk.Radiobutton(mode_box, text=label, value=value, variable=mode_var).grid(
            row=0, column=column, sticky="w", padx=(0, 18)
        )

    fields = [
        ("COM port", str(app.app_settings.get("com_port", "COM3"))),
        ("Current setpoint, mA", str(saved.get("current_setpoint_mA", "3.5"))),
        ("Voltage setpoint, V", str(saved.get("voltage_setpoint_V", "3.5"))),
        ("Voltage limit, V", str(saved.get("voltage_limit_V", "5"))),
        ("Current limit, mA", str(saved.get("current_limit_mA", "10"))),
        ("Measurement time, s", str(saved.get("measurement_time_s", "86400"))),
        ("Sample interval, s", str(saved.get("sample_interval_s", "1"))),
        ("Autosave interval, s", str(saved.get("autosave_interval_s", "600"))),
    ]
    vars_: Dict[str, tk.StringVar] = {}
    field_widgets: Dict[str, tuple[ttk.Label, ttk.Entry]] = {}
    for row_idx, (label, default) in enumerate(fields, start=2):
        label_widget = ttk.Label(frame, text=label + ":")
        label_widget.grid(row=row_idx, column=0, sticky="e", pady=3, padx=(0, 8))
        var = tk.StringVar(value=default)
        vars_[label] = var
        entry = ttk.Entry(frame, textvariable=var, width=18)
        entry.grid(row=row_idx, column=1, sticky="w", pady=3)
        field_widgets[label] = (label_widget, entry)

    hint_var = tk.StringVar()
    hint = ttk.Label(frame, textvariable=hint_var, foreground="#555555", wraplength=600, justify="left")
    hint.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 2))

    def refresh_mode(*_args) -> None:
        current_widgets = field_widgets["Current setpoint, mA"]
        voltage_widgets = field_widgets["Voltage setpoint, V"]
        if mode_var.get() == "voltage":
            for widget in current_widgets:
                widget.grid_remove()
            for widget in voltage_widgets:
                widget.grid()
            hint_var.set(
                "Напряжение задаётся напрямую и при изменении сразу подаётся на SMU. "
                "Цель можно ввести или изменить кнопками ±0.1, ±0.25, ±0.5 и ±1 В."
            )
        else:
            for widget in voltage_widgets:
                widget.grid_remove()
            for widget in current_widgets:
                widget.grid()
            hint_var.set(
                "Стартовое напряжение рассчитывается как 0.9 × V(ВАЯХ) для заданного тока. "
                "Уставку тока также можно увеличивать или уменьшать во время измерения."
            )

    mode_var.trace_add("write", refresh_mode)
    refresh_mode()

    def start() -> None:
        try:
            pid = pixel_var.get()
            mode = mode_var.get()
            voltage_limit = parse_float(vars_["Voltage limit, V"].get(), "Voltage limit")
            current_limit = parse_float(vars_["Current limit, mA"].get(), "Current limit")
            if mode == "current":
                target = parse_float(vars_["Current setpoint, mA"].get(), "Current setpoint")
                voltage_start = resolve_stability_start_voltage(app, pid, target)
                if voltage_start is None:
                    return
                maximum = current_limit
            else:
                target = parse_float(vars_["Voltage setpoint, V"].get(), "Voltage setpoint")
                if target < 0 or target > voltage_limit:
                    raise ValueError("Уставка напряжения должна быть от 0 до предела напряжения.")
                voltage_start = target
                maximum = voltage_limit

            params = build_stability_params(app, vars_, mode, target, voltage_start)
            params = params_for_pixel_luminance(app, pid, params)
            controller = StabilitySetpointController(mode, target, maximum=maximum)
            app.save_measurement_defaults(
                "stability",
                {
                    "control_mode": mode,
                    "current_setpoint_mA": vars_["Current setpoint, mA"].get(),
                    "voltage_setpoint_V": vars_["Voltage setpoint, V"].get(),
                    "voltage_limit_V": vars_["Voltage limit, V"].get(),
                    "current_limit_mA": vars_["Current limit, mA"].get(),
                    "measurement_time_s": vars_["Measurement time, s"].get(),
                    "sample_interval_s": vars_["Sample interval, s"].get(),
                    "autosave_interval_s": vars_["Autosave interval, s"].get(),
                },
            )
            if measurement_runner is not None:
                win.destroy()
                measurement_runner(
                    "stability",
                    pid,
                    lambda: measure_one_stability(app, pid, params, controller, return_to_menu=False),
                )
                return

            win.withdraw()
            result = measure_one_stability(app, pid, params, controller, return_to_menu=False)
            if result is None:
                win.deiconify()
                return
            win.destroy()
            app.show_measurement_menu()
        except Exception as exc:
            app.log(traceback.format_exc())
            messagebox.showerror("Ошибка стабильности", str(exc), parent=win)

    ttk.Button(frame, text="Открыть настройки", command=app.open_settings_window).grid(
        row=11, column=0, sticky="w", pady=18
    )
    ttk.Button(frame, text="Начать стабильность", command=start).grid(
        row=11, column=1, sticky="w", pady=18
    )
    fit_toplevel_to_content(win, 720, 760)


def measure_one_stability(
    app,
    pixel_id: str,
    params: StabilityParams,
    controller: StabilitySetpointController,
    return_to_menu: bool = True,
) -> Optional[Dict[str, Any]]:
    """Run one stability measurement and update the journal."""

    assert app.series is not None
    output_dir = ensure_measurement_folder(
        app.series.series_folder,
        "STABILITY",
        pixel_id,
        app.series.journal.get_pixel(pixel_id),
    )
    progress: Optional[StabilityProgressWindow] = None
    measurement_session = None
    try:
        progress = StabilityProgressWindow(app, pixel_id, controller)
        measurement_session = app.begin_measurement_session("STABILITY", pixel_id)
        result = run_stability_measurement(
            pixel_id,
            output_dir,
            params,
            app.log,
            app.app_settings,
            control=controller,
            progress=progress.update,
            measurement_started_monotonic=measurement_session["started_monotonic"],
        )
        measurement_session["events"] = list(result.get("events") or [])
        notes = "Остановлено пользователем" if result.get("stopped_by_user") else ""
        journal_params = params.as_dict()
        journal_params["final_setpoint"] = result.get("final_setpoint")
        app.series.journal.update_after_measurement(
            "STABILITY", pixel_id, result["status"], result["file"], journal_params, notes=notes
        )
        app.log(f"Стабильность завершена: {pixel_id}, файл {result['file'].name}")
        app.refresh_pixel_table()
        if return_to_menu:
            app.show_measurement_menu()
        return result
    except Exception as exc:
        app.log(traceback.format_exc())
        messagebox.showerror("Ошибка стабильности", str(exc), parent=app)
        return None
    finally:
        if measurement_session is not None:
            app.end_measurement_session(measurement_session)
        if progress is not None:
            progress.close()


def resolve_stability_start_voltage(app, pixel_id: str, target_current_mA: float) -> Optional[float]:
    assert app.series is not None
    pixel = app.series.journal.get_pixel(pixel_id)
    ivl_rel = pixel.get("Last IVL file") if pixel else ""
    ivl_file = app.series.series_folder / ivl_rel if ivl_rel else None
    v_at_current = interpolate_voltage_at_current_from_ivl(ivl_file, target_current_mA) if ivl_file else None
    if v_at_current is None:
        manual = simpledialog.askfloat(
            "Стартовое напряжение",
            f"Не удалось найти V в ВАЯХ для {target_current_mA:g} мА.\n"
            "Введите напряжение, соответствующее этому току по ВАХ.",
            parent=app,
        )
        if manual is None:
            return None
        v_at_current = manual

    voltage_start = 0.9 * float(v_at_current)
    confirmed = messagebox.askokcancel(
        "Проверка старта",
        f"Для {pixel_id}: V по ВАЯХ при {target_current_mA:g} мА ≈ {v_at_current:.3f} В.\n"
        f"Старт стабильности будет {voltage_start:.3f} В, то есть на 10% ниже.\n\nПродолжить?",
        parent=app,
    )
    return voltage_start if confirmed else None


def build_stability_params(
    app,
    vars_: Dict[str, tk.StringVar],
    mode: str,
    target: float,
    voltage_start: float,
) -> StabilityParams:
    adv = app.app_settings.get("stability_advanced", DEFAULT_APP_SETTINGS["stability_advanced"])
    units = app.app_settings.get("measurement_units", DEFAULT_APP_SETTINGS["measurement_units"])
    return StabilityParams(
        com_port=effective_com_port({**app.app_settings, "com_port": vars_["COM port"].get().strip()}, app.log),
        control_mode=mode,
        current_setpoint_mA=target if mode == "current" else parse_float(
            vars_["Current setpoint, mA"].get(), "Current setpoint"
        ),
        voltage_setpoint_V=target if mode == "voltage" else parse_float(
            vars_["Voltage setpoint, V"].get(), "Voltage setpoint"
        ),
        voltage_start=voltage_start,
        voltage_limit=parse_float(vars_["Voltage limit, V"].get(), "Voltage limit"),
        current_limit_mA=parse_float(vars_["Current limit, mA"].get(), "Current limit"),
        voltage_step_max=float(adv.get("voltage_step_max", 0.02)),
        current_control_kp=float(adv.get("current_control_kp", 0.01)),
        measurement_time_s=parse_float(vars_["Measurement time, s"].get(), "Measurement time"),
        sample_interval_s=parse_float(vars_["Sample interval, s"].get(), "Sample interval"),
        autosave_interval_s=parse_float(vars_["Autosave interval, s"].get(), "Autosave interval"),
        photodiode_bias_V=float(adv.get("photodiode_bias_V", -5.0)),
        photodiode_threshold_uA=float(adv.get("photodiode_threshold_uA", 0.1)),
        photodiode_range=int(adv.get("photodiode_range", 4)),
        pixel_area_mm2=float(units.get("pixel_area_mm2", 1.0)),
        luminance_cd_m2_per_uA=float(units.get("luminance_cd_m2_per_uA", 1.0)),
        geometric_coefficient=float(units.get("geometric_conversion_coefficient", 1.0)),
    )


def params_for_pixel_luminance(app, pixel_id: str, params: StabilityParams) -> StabilityParams:
    if app.series is None:
        return params
    coeff = app.series.luminance_coefficient_for_pixel(pixel_id, app.app_settings)
    return replace(params, luminance_cd_m2_per_uA=coeff)
