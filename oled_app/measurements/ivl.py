"""IVL measurement workflow for the modular OLED application."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from oled_app.hardware import prepare_hardware_environment, safe_shutdown_smu
from oled_app.measurements.raw_io import RawCsvWriter, cleanup_raw_files, raw_csv_path
from oled_app.processing.ivl_results import (
    IVL_RAW_HEADERS,
    build_ivl_workbook_from_raw_csv,
    confirmed_burned_cycle,
    describe_ivl_first_measurement as describe_ivl_result,
    final_ivl_status,
    save_ivl_workbook as write_ivl_workbook,
)
from oled_app.processing.ivl_preview import create_ivl_thumbnail, ivl_thumbnail_path
from oled_app.utils import (
    current_density_mA_cm2,
    luminance_cd_m2_at_voltage,
    now_str,
    safe_filename,
    timestamp_for_file,
)


class MeasurementStopped(Exception):
    """Raised by progress callbacks when a measurement should stop."""


@dataclass
class IVLParams:
    com_port: str = "COM3"
    sweep_start: float = 0.0
    sweep_end: float = 5.0
    sweep_increment: float = 0.02
    sweep_time_per_point: float = 0.01
    num_cycles: int = 1
    delay_between_cycles: float = 1.0
    current_limit_mA: float = 10.0
    photodiode_bias_V: float = -5.0
    photodiode_range: int = 4
    photodiode_threshold_uA: float = 0.5
    working_confirmation_points: int = 5
    opening_photodiode_threshold_uA: float = 0.5
    opening_confirmation_points: int = 5
    burnout_current_threshold_mA: float = 10.0
    mark_current_limit_as_burnout: bool = False
    no_contact_max_led_current_mA: float = 0.05
    burned_confirmation_cycles: int = 1
    pixel_area_mm2: float = 1.0
    luminance_cd_m2_per_uA: float = 1.0
    geometric_coefficient: float = 1.0
    luminance_calibration_model: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def define_ivl_pixel_status(
    max_photo_uA: float,
    max_led_current_mA: float,
    current_limit_reached: bool,
    params: IVLParams,
    cycle_data: List[Dict[str, Any]],
) -> Tuple[str, str]:
    light_detected = first_sustained_photodiode_index(
        cycle_data,
        params.photodiode_threshold_uA,
        params.working_confirmation_points,
    ) is not None
    burnout_by_high_current = max_led_current_mA >= params.burnout_current_threshold_mA

    if burnout_by_high_current:
        return "BURNED", "Пробой / сгорание по току"
    if light_detected:
        return (
            "WORKING",
            "Рабочий: устойчивый фототок выше порога "
            f"({params.working_confirmation_points} следующих точек)",
        )
    if max_led_current_mA <= params.no_contact_max_led_current_mA:
        return "NO_CONTACT", "Нет контакта: ток почти нулевой"
    return "NONWORKING", "Нерабочий: ток есть, фототока нет"


def first_sustained_photodiode_index(
    cycle_data: List[Dict[str, Any]],
    threshold_uA: float,
    confirmation_points: int = 0,
) -> Optional[int]:
    """Return the first sustained threshold-crossing row index.

    ``confirmation_points`` is the number of points *after* the candidate that
    must also remain at or above the photodiode-current threshold.
    """

    if threshold_uA < 0:
        raise ValueError("Порог фототока для открытия не может быть отрицательным.")
    if confirmation_points < 0:
        raise ValueError("Количество подтверждающих точек не может быть отрицательным.")

    window_size = int(confirmation_points) + 1
    last_candidate = len(cycle_data) - window_size
    for candidate_idx in range(last_candidate + 1):
        window = cycle_data[candidate_idx : candidate_idx + window_size]
        if all(float(row.get("Photodiode current (uA)", 0.0)) >= threshold_uA for row in window):
            return candidate_idx
    return None


def detect_opening_voltage(
    cycle_data: List[Dict[str, Any]],
    threshold_uA: float,
    confirmation_points: int = 0,
) -> Optional[float]:
    """Return the first threshold crossing confirmed by following points."""

    candidate_idx = first_sustained_photodiode_index(
        cycle_data,
        threshold_uA,
        confirmation_points,
    )
    if candidate_idx is None:
        return None
    row = cycle_data[candidate_idx]
    return float(
        row.get("Voltage OLED / LED measured (V)", row.get("Voltage set (V)", 0))
    )


def describe_ivl_first_measurement(cycles: List[Dict[str, Any]]) -> str:
    return describe_ivl_result(cycles)


def run_ivl_cycle(
    smu,
    pixel_id: str,
    cycle_number: int,
    params: IVLParams,
    log: Callable[[str], None],
    progress_callback: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    raw_writer: Optional[RawCsvWriter] = None,
    measurement_started_monotonic: Optional[float] = None,
) -> Dict[str, Any]:
    log(f"\nВАЯХ {pixel_id}, цикл {cycle_number}: до {params.sweep_end:.3f} В, лимит {params.current_limit_mA:.3f} мА")

    smu.smu1.set.enabled(True, response=0)
    smu.smu2.set.enabled(True, response=0)
    try:
        smu.smu2.set.range(params.photodiode_range, response=0)
    except Exception:
        pass

    time.sleep(0.1)
    smu.smu1.set.voltage(0, response=0)
    smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
    time.sleep(0.2)

    data: List[Dict[str, Any]] = []
    current_limit_reached = False
    current_limit_elapsed_s: Optional[float] = None
    current_limit_point: Optional[int] = None

    voltage_values = np.arange(
        params.sweep_start,
        params.sweep_end + params.sweep_increment / 2,
        params.sweep_increment,
    )
    voltage_values = np.round(voltage_values, 6)

    for idx, set_v in enumerate(voltage_values, start=1):
        smu.smu1.set.voltage(float(set_v), response=0)
        smu.smu2.set.voltage(params.photodiode_bias_V, response=0)
        time.sleep(params.sweep_time_per_point)

        voltage_led, current_led = smu.smu1.measure()[0]
        voltage_pd, current_pd = smu.smu2.measure()[0]
        current_led_mA = current_led * 1000.0
        current_pd_uA = -current_pd * 1_000_000.0
        date_time = now_str()
        measurement_elapsed_s = (
            time.monotonic() - measurement_started_monotonic
            if measurement_started_monotonic is not None
            else 0.0
        )

        point_row = {
            "Point": idx,
            "Voltage set (V)": float(set_v),
            "Voltage OLED / LED measured (V)": float(voltage_led),
            "Current OLED / LED (mA)": float(current_led_mA),
            "Current density (mA/cm^2)": current_density_mA_cm2(current_led_mA, params.pixel_area_mm2),
            "Voltage photodiode measured (V)": float(voltage_pd),
            "Photodiode current (uA)": float(current_pd_uA),
            "Luminance (cd/m^2)": luminance_cd_m2_at_voltage(
                current_pd_uA,
                params.luminance_cd_m2_per_uA,
                set_v,
                params.luminance_calibration_model,
            ),
            "Measurement time (s)": float(measurement_elapsed_s),
        }
        if raw_writer is not None:
            raw_writer.writerow(
                {
                    "cycle": cycle_number,
                    "point": idx,
                    "date_time": date_time,
                    "elapsed_s": float(measurement_elapsed_s),
                    "voltage_set_V": float(set_v),
                    "voltage_led_measured_V": float(voltage_led),
                    "current_led_A": float(current_led),
                    "voltage_photodiode_measured_V": float(voltage_pd),
                    "current_photodiode_A": float(current_pd),
                    "current_led_mA": float(current_led_mA),
                    "current_photodiode_uA": float(current_pd_uA),
                }
            )
        data.append(point_row)
        if progress_callback is not None:
            try:
                progress_callback(cycle_number, point_row)
            except MeasurementStopped:
                safe_shutdown_smu(smu)
                raise

        if idx == 1 or idx % 25 == 0:
            log(f"  {idx}/{len(voltage_values)}: V={voltage_led:.3f} В, I={current_led_mA:.3f} мА, PD={current_pd_uA:.3f} мкА")

        if current_led_mA >= params.current_limit_mA:
            current_limit_reached = True
            current_limit_elapsed_s = float(measurement_elapsed_s)
            current_limit_point = idx
            log(f"  Аварийный стоп: ток {current_led_mA:.3f} мА >= {params.current_limit_mA:.3f} мА")
            try:
                smu.smu1.set.voltage(0, response=0)
            except Exception:
                pass
            break

    safe_shutdown_smu(smu)

    max_photo = max([row["Photodiode current (uA)"] for row in data], default=0.0)
    max_current = max([row["Current OLED / LED (mA)"] for row in data], default=0.0)
    status, status_desc = define_ivl_pixel_status(
        max_photo,
        max_current,
        current_limit_reached,
        params,
        data,
    )
    opening = detect_opening_voltage(
        data,
        params.opening_photodiode_threshold_uA,
        params.opening_confirmation_points,
    )

    log(f"  Цикл {cycle_number}: статус {status}, max I={max_current:.3f} мА, max PD={max_photo:.3f} мкА")
    return {
        "cycle": cycle_number,
        "status": status,
        "status_desc": status_desc,
        "current_limit_reached": current_limit_reached,
        "current_limit_elapsed_s": current_limit_elapsed_s,
        "current_limit_point": current_limit_point,
        "max_photo_uA": max_photo,
        "max_current_mA": max_current,
        "opening_voltage": opening,
        "data": data,
    }


def save_ivl_workbook(pixel_id: str, output_dir: Path, params: IVLParams, cycles: List[Dict[str, Any]]) -> Path:
    filename = output_dir / f"IVL_{safe_filename(pixel_id)}_{timestamp_for_file()}.xlsx"
    return write_ivl_workbook(pixel_id, filename, params, cycles)


def run_ivl_measurement(
    pixel_id: str,
    output_dir: Path,
    params: IVLParams,
    log: Callable[[str], None],
    app_settings: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    measurement_started_monotonic: Optional[float] = None,
) -> Dict[str, Any]:
    prepare_hardware_environment(pixel_id, app_settings, log)
    import xtralien

    measurement_timestamp = timestamp_for_file()
    file_stem = f"IVL_{safe_filename(pixel_id)}_{measurement_timestamp}"
    filename = output_dir / f"{file_stem}.xlsx"
    raw_file = raw_csv_path(output_dir, f"{file_stem}_raw.csv", app_settings)
    log(f"Raw CSV ВАЯХ: {raw_file}")

    cycles: List[Dict[str, Any]] = []
    measurement_started_monotonic = measurement_started_monotonic or time.monotonic()
    cycles_to_run = max(1, int(params.num_cycles))
    burned_confirmations_left = max(0, int(params.burned_confirmation_cycles))
    with RawCsvWriter(raw_file, IVL_RAW_HEADERS) as raw_writer:
        with xtralien.Device(params.com_port) as smu:
            cycle = 1
            while cycle <= cycles_to_run:
                cycle_result = run_ivl_cycle(
                    smu,
                    pixel_id,
                    cycle,
                    params,
                    log,
                    progress_callback=progress_callback,
                    raw_writer=raw_writer,
                    measurement_started_monotonic=measurement_started_monotonic,
                )
                cycles.append(cycle_result)
                if cycle_result["status"] == "BURNED" and burned_confirmations_left > 0:
                    burned_confirmations_left -= 1
                    cycles_to_run = max(cycles_to_run, cycle + 1)
                    log("  BURNED: запускается дополнительный подтверждающий цикл.")
                elif cycle_result["status"] in {"BURNED", "NO_CONTACT", "NONWORKING"}:
                    log(f"  Дальнейшие циклы остановлены: {cycle_result['status']}")
                    break
                if cycle < cycles_to_run:
                    time.sleep(params.delay_between_cycles)
                cycle += 1

    filename = build_ivl_workbook_from_raw_csv(raw_file, filename, pixel_id, params, cycles)
    thumbnail = create_ivl_thumbnail(
        ivl_thumbnail_path(filename, pixel_id),
        cycles,
    )
    kept_raw_files = cleanup_raw_files([raw_file], app_settings, log)
    best_opening = next((cycle.get("opening_voltage") for cycle in cycles if cycle.get("opening_voltage") is not None), None)
    max_current = max([cycle["max_current_mA"] for cycle in cycles], default=0.0)
    max_photo = max([cycle["max_photo_uA"] for cycle in cycles], default=0.0)
    ivl_diagnosis = describe_ivl_first_measurement(cycles)
    burned_cycle = confirmed_burned_cycle(cycles)
    final_status = final_ivl_status(cycles)
    events = []
    for cycle_result in cycles:
        event_time = cycle_result.get("current_limit_elapsed_s")
        if event_time is not None:
            events.append(
                {
                    "event": "current_limit_or_breakdown",
                    "label": "Лимит тока / возможный пробой или шунт",
                    "measurement_time_s": float(event_time),
                    "cycle": int(cycle_result.get("cycle") or 1),
                    "point": int(cycle_result.get("current_limit_point") or 0),
                }
            )
    return {
        "file": filename,
        "thumbnail": thumbnail,
        "raw_file": kept_raw_files[0] if kept_raw_files else None,
        "status": final_status,
        "opening_voltage": best_opening,
        "max_current_mA": max_current,
        "max_photo_uA": max_photo,
        "ivl_diagnosis": ivl_diagnosis,
        "burned_cycle": burned_cycle,
        "first_cycle_status": cycles[0]["status"] if cycles else "FAILED",
        "events": events,
    }
