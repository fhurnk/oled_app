"""Spectrum measurement window for the modular OLED app."""

from __future__ import annotations

import tkinter as tk
import traceback
from tkinter import messagebox, ttk
from typing import Dict

from oled_app.hardware import effective_com_port
from oled_app.measurements.spectrum import SpectrumParams, run_spectrum_measurement
from oled_app.series import ensure_measurement_folder
from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_app.utils import as_float_or_none, parse_float

from .progress import SpectrumProgressWindow
from .widgets import fit_toplevel_to_content


def open_spectrum_window(app) -> None:
    if app.series is None:
        return
    pixels = app.pixel_ids(require_ivl=True, require_opening=True)
    if not pixels:
        messagebox.showwarning("Спектры", "В журнале нет пикселей с ВАЯХ и заданным напряжением открытия.", parent=app)
        return

    win = tk.Toplevel(app)
    win.title("Спектры")
    win.geometry("560x560")
    win.transient(app)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Пиксель:").grid(row=0, column=0, sticky="e", pady=5)
    pixel_var = tk.StringVar(value=pixels[0])
    pixel_combo = ttk.Combobox(frame, values=pixels, textvariable=pixel_var, state="readonly", width=26)
    pixel_combo.grid(row=0, column=1, sticky="w", pady=5)

    first_pixel = app.series.journal.get_pixel(pixels[0])
    first_opening = as_float_or_none(first_pixel.get("Opening voltage (V)")) if first_pixel else None
    saved_spectrum = app.measurement_defaults("spectrum")
    use_opening_var = tk.BooleanVar(value=bool(saved_spectrum.get("use_opening_voltage", True)))
    opening_info_var = tk.StringVar(value=f"V открытия: {first_opening:.3f} В" if first_opening is not None else "V открытия: нет")

    fields = [
        ("COM port", str(app.app_settings.get("com_port", "COM3"))),
        ("Voltage start, V", f"{first_opening:.3f}" if first_opening is not None else "2.0"),
        ("Voltage end, V", str(saved_spectrum.get("voltage_end_V", "5"))),
        ("Voltage step, V", str(saved_spectrum.get("voltage_step_V", "0.1"))),
        ("Current limit, mA", str(saved_spectrum.get("current_limit_mA", "6"))),
        ("LED type", str(saved_spectrum.get("led_type", "auto"))),
    ]
    vars_: Dict[str, tk.StringVar] = {}
    for row_idx, (label, default) in enumerate(fields, start=1):
        ttk.Label(frame, text=label + ":").grid(row=row_idx, column=0, sticky="e", pady=3, padx=(0, 8))
        var = tk.StringVar(value=default)
        vars_[label] = var
        ttk.Entry(frame, textvariable=var, width=18).grid(row=row_idx, column=1, sticky="w", pady=3)

    ttk.Checkbutton(frame, text="Стартовать от V открытия из журнала", variable=use_opening_var).grid(
        row=len(fields) + 1,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(8, 2),
    )
    ttk.Label(frame, textvariable=opening_info_var, foreground="#555555").grid(
        row=len(fields) + 2,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(0, 2),
    )

    def update_opening_info(_event=None) -> None:
        pixel = app.series.journal.get_pixel(pixel_var.get()) if app.series is not None else None
        opening = as_float_or_none(pixel.get("Opening voltage (V)")) if pixel else None
        opening_info_var.set(f"V открытия: {opening:.3f} В" if opening is not None else "V открытия: нет")
        if opening is not None and use_opening_var.get():
            vars_["Voltage start, V"].set(f"{opening:.3f}")

    pixel_combo.bind("<<ComboboxSelected>>", update_opening_info)

    def start() -> None:
        progress = None
        try:
            pid = pixel_var.get()
            pixel = app.series.journal.get_pixel(pid)
            opening = as_float_or_none(pixel.get("Opening voltage (V)")) if pixel else None
            if opening is None:
                raise ValueError("Для выбранного пикселя нет напряжения открытия")
            params = build_spectrum_params(app, vars_, opening, bool(use_opening_var.get()))
            app.save_measurement_defaults("spectrum", {
                "voltage_end_V": vars_["Voltage end, V"].get(),
                "voltage_step_V": vars_["Voltage step, V"].get(),
                "current_limit_mA": vars_["Current limit, mA"].get(),
                "led_type": vars_["LED type"].get(),
                "use_opening_voltage": bool(use_opening_var.get()),
            })
            output_dir = ensure_measurement_folder(
                app.series.series_folder,
                "SPECTRUM",
                pid,
                app.series.journal.get_pixel(pid),
            )
            progress = SpectrumProgressWindow(app, pid)
            result = run_spectrum_measurement(
                pid,
                output_dir,
                params,
                app.log,
                app.app_settings,
                progress_callback=progress.update_spectrum,
            )
            progress.close()
            app.series.journal.update_after_measurement(
                "SPECTRUM",
                pid,
                result["status"],
                result["file"],
                params.as_dict(),
                spectrum_peak_count=result.get("spectrum_peak_count"),
                spectrum_peaks_nm=result.get("spectrum_peaks_nm", ""),
                spectrum_max_intensity=result.get("spectrum_max_intensity"),
            )
            app.log(f"Спектры завершены: {pid}, файл {result['file'].name}")
            app.refresh_pixel_table()
            app.show_measurement_menu()
            win.destroy()
        except Exception as exc:
            if progress is not None:
                progress.close()
            app.log(traceback.format_exc())
            messagebox.showerror("Ошибка спектров", str(exc), parent=win)

    ttk.Label(
        frame,
        text="V открытия остается в журнале. Для спектра можно временно выбрать другое стартовое напряжение.",
        foreground="#555555",
        wraplength=500,
    ).grid(row=len(fields) + 3, column=0, columnspan=2, sticky="w", pady=(8, 2))
    ttk.Button(frame, text="Открыть настройки", command=app.open_settings_window).grid(
        row=len(fields) + 4,
        column=0,
        sticky="w",
        pady=16,
    )
    ttk.Button(frame, text="Начать съемку спектров", command=start).grid(
        row=len(fields) + 4,
        column=1,
        sticky="w",
        pady=16,
    )
    fit_toplevel_to_content(win, 620, 650)


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
        max_peak_width_nm=float(adv.get("max_peak_width_nm", 150.0)),
        t_int_initial_s=float(adv.get("t_int_initial_s", 0.01)),
        t_int_min_s=float(adv.get("t_int_min_s", 0.001)),
        t_int_max_s=float(adv.get("t_int_max_s", 10.0)),
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
