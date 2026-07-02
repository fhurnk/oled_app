"""Stability measurement workflow for the modular OLED application."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from openpyxl import load_workbook

from oled_app.hardware import prepare_hardware_environment, safe_shutdown_smu
from oled_app.measurements.raw_io import RawCsvWriter, cleanup_raw_files, raw_csv_path
from oled_app.processing.stability_results import (
    STABILITY_RAW_HEADERS,
    build_stability_workbook_from_raw_csv,
    create_stability_workbook as write_stability_workbook,
    save_stability_chart as write_stability_chart,
    update_stability_status as write_stability_status,
)
from oled_app.utils import (
    as_float_or_none,
    current_density_mA_cm2,
    luminance_cd_m2,
    now_str,
    safe_filename,
    timestamp_for_file,
)


@dataclass
class StabilityParams:
    com_port: str = "COM3"
    current_setpoint_mA: float = 3.5
    voltage_start: float = 3.5
    voltage_limit: float = 5.0
    current_limit_mA: float = 10.0
    voltage_step_max: float = 0.02
    current_control_kp: float = 0.01
    measurement_time_s: float = 86400.0
    sample_interval_s: float = 1.0
    autosave_interval_s: float = 600.0
    photodiode_bias_V: float = -5.0
    photodiode_threshold_uA: float = 0.1
    photodiode_range: int = 4
    pixel_area_mm2: float = 1.0
    luminance_cd_m2_per_uA: float = 1.0

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def find_ivl_data_columns(ws) -> Optional[Tuple[int, Dict[str, int]]]:
    wanted = {
        "Voltage OLED / LED measured (V)": None,
        "Current OLED / LED (mA)": None,
    }
    for row in range(1, min(ws.max_row, 30) + 1):
        headers = {}
        for column in range(1, ws.max_column + 1):
            value = ws.cell(row=row, column=column).value
            if value in wanted:
                headers[value] = column
        if all(key in headers for key in wanted):
            return row, headers
    return None


def interpolate_voltage_at_current_from_ivl(ivl_file: Path, target_current_mA: float) -> Optional[float]:
    ivl_file = Path(ivl_file)
    if not ivl_file.exists():
        return None

    wb = load_workbook(ivl_file, data_only=True)
    points: List[Tuple[float, float]] = []
    try:
        for sheet_name in wb.sheetnames:
            if not sheet_name.startswith("Cycle_"):
                continue
            ws = wb[sheet_name]
            found = find_ivl_data_columns(ws)
            if not found:
                continue
            header_row, cols = found
            v_col = cols["Voltage OLED / LED measured (V)"]
            i_col = cols["Current OLED / LED (mA)"]
            for row in range(header_row + 1, ws.max_row + 1):
                voltage = as_float_or_none(ws.cell(row=row, column=v_col).value)
                current = as_float_or_none(ws.cell(row=row, column=i_col).value)
                if voltage is not None and current is not None and math.isfinite(voltage) and math.isfinite(current):
                    points.append((current, voltage))
    finally:
        wb.close()

    if len(points) < 2:
        return None
    points = sorted(points, key=lambda item: item[0])

    if target_current_mA <= points[0][0]:
        return points[0][1]
    if target_current_mA >= points[-1][0]:
        return points[-1][1]

    for (i1, v1), (i2, v2) in zip(points[:-1], points[1:]):
        if i1 <= target_current_mA <= i2 and i2 != i1:
            ratio = (target_current_mA - i1) / (i2 - i1)
            return v1 + ratio * (v2 - v1)
    return min(points, key=lambda item: abs(item[0] - target_current_mA))[1]


def create_stability_workbook(filename: Path, pixel_id: str, params: StabilityParams):
    return write_stability_workbook(filename, pixel_id, params)


def update_stability_status(ws, status: str, max_photo: float, elapsed: float) -> None:
    write_stability_status(ws, status, max_photo, elapsed)


def save_stability_chart(filename: Path, pixel_id: str) -> None:
    write_stability_chart(filename, pixel_id)


def run_stability_measurement(
    pixel_id: str,
    output_dir: Path,
    params: StabilityParams,
    log: Callable[[str], None],
    app_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prepare_hardware_environment(pixel_id, app_settings, log)
    import xtralien

    output_dir.mkdir(parents=True, exist_ok=True)
    measurement_timestamp = timestamp_for_file()
    file_stem = f"STABILITY_{safe_filename(pixel_id)}_{params.current_setpoint_mA:g}mA_{measurement_timestamp}"
    filename = output_dir / f"{file_stem}.xlsx"
    raw_file = raw_csv_path(output_dir, f"{file_stem}_raw.csv", app_settings)
    log(f"Raw CSV стабильности: {raw_file}")

    max_photo = 0.0
    point_number = 0
    last_autosave_elapsed = 0.0
    voltage_set = params.voltage_start
    current_limit_reached = False
    voltage_limit_reached = False
    last_elapsed = 0.0

    with RawCsvWriter(raw_file, STABILITY_RAW_HEADERS) as raw_writer:
        with xtralien.Device(params.com_port) as smu:
            try:
                smu.smu1.set.enabled(True, response=0)
                smu.smu2.set.enabled(True, response=0)
                try:
                    smu.smu2.set.range(params.photodiode_range, response=0)
                except Exception:
                    pass
                time.sleep(0.1)
                smu.smu1.set.voltage(0, response=0)
                smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
                time.sleep(0.3)

                start = time.time()
                next_point = start
                log(f"Стабильность {pixel_id}: I={params.current_setpoint_mA:.3f} мА, старт V={voltage_set:.3f} В")
                while True:
                    now = time.time()
                    elapsed = now - start
                    if elapsed > params.measurement_time_s:
                        break
                    if now < next_point:
                        time.sleep(next_point - now)
                    next_point += params.sample_interval_s
                    now = time.time()
                    elapsed = now - start
                    last_elapsed = elapsed

                    voltage_set_for_point = float(voltage_set)
                    smu.smu1.set.voltage(voltage_set_for_point, response=0)
                    smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
                    time.sleep(0.02)
                    v_led, i_led = smu.smu1.measure()[0]
                    v_pd, i_pd = smu.smu2.measure()[0]
                    i_led_mA = i_led * 1000.0
                    i_pd_uA = -i_pd * 1_000_000.0
                    j_led = current_density_mA_cm2(i_led_mA, params.pixel_area_mm2)
                    lum = luminance_cd_m2(i_pd_uA, params.luminance_cd_m2_per_uA)

                    max_photo = max(max_photo, i_pd_uA)
                    point_number += 1
                    raw_writer.writerow(
                        {
                            "point": point_number,
                            "date_time": now_str(),
                            "elapsed_s": elapsed,
                            "current_setpoint_mA": params.current_setpoint_mA,
                            "voltage_set_V": voltage_set_for_point,
                            "voltage_led_measured_V": float(v_led),
                            "current_led_A": float(i_led),
                            "voltage_photodiode_measured_V": float(v_pd),
                            "current_photodiode_A": float(i_pd),
                            "current_led_mA": float(i_led_mA),
                            "current_density_mA_cm2": j_led,
                            "current_photodiode_uA": float(i_pd_uA),
                            "luminance_cd_m2": lum,
                        }
                    )

                    if i_led_mA > params.current_limit_mA:
                        current_limit_reached = True
                        log(f"Аварийный стоп: ток {i_led_mA:.3f} мА > {params.current_limit_mA:.3f} мА")
                        try:
                            smu.smu1.set.voltage(0, response=0)
                        except Exception:
                            pass

                    if not current_limit_reached:
                        error_mA = params.current_setpoint_mA - i_led_mA
                        d_v = params.current_control_kp * error_mA
                        d_v = float(np.clip(d_v, -params.voltage_step_max, params.voltage_step_max))
                        voltage_set += d_v
                        voltage_set = max(0.0, voltage_set)
                        if voltage_set >= params.voltage_limit:
                            voltage_set = params.voltage_limit
                            voltage_limit_reached = True
                            log(f"Достигнут предел напряжения {params.voltage_limit:.3f} В")

                    if point_number == 1 or point_number % 60 == 0:
                        log(f"  t={elapsed:.1f} c; V={v_led:.3f} В; I={i_led_mA:.3f} мА; PD={i_pd_uA:.3f} мкА")

                    need_save = (
                        (elapsed - last_autosave_elapsed >= params.autosave_interval_s)
                        or current_limit_reached
                        or voltage_limit_reached
                    )
                    if need_save:
                        last_autosave_elapsed = elapsed
                        log(f"  Raw CSV сохранен: {raw_file.name}, t={elapsed:.1f} c")

                    if current_limit_reached or voltage_limit_reached:
                        break
            finally:
                safe_shutdown_smu(smu)

    if current_limit_reached:
        status = "BURNED"
    elif voltage_limit_reached and max_photo < params.photodiode_threshold_uA:
        status = "NO_CONTACT"
    elif max_photo >= params.photodiode_threshold_uA:
        status = "WORKING"
    else:
        status = "NONWORKING"

    filename = build_stability_workbook_from_raw_csv(
        raw_file,
        filename,
        pixel_id,
        params,
        status,
        max_photo,
        last_elapsed,
    )
    kept_raw_files = cleanup_raw_files([raw_file], app_settings, log)
    return {
        "file": filename,
        "raw_file": kept_raw_files[0] if kept_raw_files else None,
        "status": status,
        "max_photo_uA": max_photo,
    }
