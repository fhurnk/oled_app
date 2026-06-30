"""Stability measurement window for the modular OLED app."""

from __future__ import annotations

import tkinter as tk
import traceback
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, Optional

from oled_app.hardware import effective_com_port
from oled_app.measurements.stability import (
    StabilityParams,
    interpolate_voltage_at_current_from_ivl,
    run_stability_measurement,
)
from oled_app.series import ensure_measurement_folder
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import parse_float

from .widgets import fit_toplevel_to_content


def open_stability_window(app) -> None:
    if app.series is None:
        return
    pixels = app.pixel_ids(require_ivl=True)
    if not pixels:
        messagebox.showwarning("Стабильность", "В журнале нет пикселей с ВАЯХ.", parent=app)
        return

    win = tk.Toplevel(app)
    win.title("Стабильность по току")
    win.geometry("600x560")
    win.transient(app)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Пиксель:").grid(row=0, column=0, sticky="e", pady=5)
    pixel_var = tk.StringVar(value=pixels[0])
    ttk.Combobox(frame, values=pixels, textvariable=pixel_var, state="readonly", width=26).grid(row=0, column=1, sticky="w", pady=5)

    saved_stability = app.measurement_defaults("stability")
    fields = [
        ("COM port", str(app.app_settings.get("com_port", "COM3"))),
        ("Current setpoint, mA", str(saved_stability.get("current_setpoint_mA", "3.5"))),
        ("Voltage limit, V", str(saved_stability.get("voltage_limit_V", "5"))),
        ("Current limit, mA", str(saved_stability.get("current_limit_mA", "10"))),
        ("Measurement time, s", str(saved_stability.get("measurement_time_s", "86400"))),
        ("Sample interval, s", str(saved_stability.get("sample_interval_s", "1"))),
        ("Autosave interval, s", str(saved_stability.get("autosave_interval_s", "600"))),
    ]
    vars_: Dict[str, tk.StringVar] = {}
    for row_idx, (label, default) in enumerate(fields, start=1):
        ttk.Label(frame, text=label + ":").grid(row=row_idx, column=0, sticky="e", pady=3, padx=(0, 8))
        var = tk.StringVar(value=default)
        vars_[label] = var
        ttk.Entry(frame, textvariable=var, width=18).grid(row=row_idx, column=1, sticky="w", pady=3)

    ttk.Label(
        frame,
        text="Стартовое напряжение будет рассчитано как 0.9 x V(ВАЯХ) для заданного тока. Остальное - Настройки -> Стабильность доп.",
        foreground="#555555",
        wraplength=540,
    ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(8, 2))

    def start() -> None:
        try:
            pid = pixel_var.get()
            target_current = parse_float(vars_["Current setpoint, mA"].get(), "Current setpoint")
            voltage_start = resolve_stability_start_voltage(app, pid, target_current)
            if voltage_start is None:
                return
            params = build_stability_params(app, vars_, target_current, voltage_start)
            app.save_measurement_defaults("stability", {
                "current_setpoint_mA": vars_["Current setpoint, mA"].get(),
                "voltage_limit_V": vars_["Voltage limit, V"].get(),
                "current_limit_mA": vars_["Current limit, mA"].get(),
                "measurement_time_s": vars_["Measurement time, s"].get(),
                "sample_interval_s": vars_["Sample interval, s"].get(),
                "autosave_interval_s": vars_["Autosave interval, s"].get(),
            })
            output_dir = ensure_measurement_folder(
                app.series.series_folder,
                "STABILITY",
                pid,
                app.series.journal.get_pixel(pid),
            )
            result = run_stability_measurement(pid, output_dir, params, app.log, app.app_settings)
            app.series.journal.update_after_measurement("STABILITY", pid, result["status"], result["file"], params.as_dict())
            app.log(f"Стабильность завершена: {pid}, файл {result['file'].name}")
            app.refresh_pixel_table()
            app.show_measurement_menu()
            win.destroy()
        except Exception as exc:
            app.log(traceback.format_exc())
            messagebox.showerror("Ошибка стабильности", str(exc), parent=win)

    ttk.Button(frame, text="Открыть настройки", command=app.open_settings_window).grid(row=len(fields) + 3, column=0, sticky="w", pady=16)
    ttk.Button(frame, text="Начать стабильность", command=start).grid(row=len(fields) + 3, column=1, sticky="w", pady=16)
    fit_toplevel_to_content(win, 660, 650)


def resolve_stability_start_voltage(app, pixel_id: str, target_current_mA: float) -> Optional[float]:
    assert app.series is not None
    pixel = app.series.journal.get_pixel(pixel_id)
    ivl_rel = pixel.get("Last IVL file") if pixel else ""
    ivl_file = app.series.series_folder / ivl_rel if ivl_rel else None
    v_at_current = interpolate_voltage_at_current_from_ivl(ivl_file, target_current_mA) if ivl_file else None
    if v_at_current is None:
        manual = simpledialog.askfloat(
            "Стартовое напряжение",
            f"Не удалось найти V в ВАЯХ для {target_current_mA:g} мА.\nВведите напряжение, соответствующее этому току по ВАХ.",
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
    target_current_mA: float,
    voltage_start: float,
) -> StabilityParams:
    adv = app.app_settings.get("stability_advanced", DEFAULT_APP_SETTINGS["stability_advanced"])
    units = app.app_settings.get("measurement_units", DEFAULT_APP_SETTINGS["measurement_units"])
    return StabilityParams(
        com_port=effective_com_port({**app.app_settings, "com_port": vars_["COM port"].get().strip()}, app.log),
        current_setpoint_mA=target_current_mA,
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
    )
