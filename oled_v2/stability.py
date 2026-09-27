"""Backend-owned single-pixel stability sessions for the v2 simulator."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import threading
from typing import Any, Dict, Optional
from uuid import uuid4

from oled_app.constants import HARDWARE_MODE_SIM
from oled_app.measurements.stability import (
    StabilityParams,
    StabilitySetpointController,
    run_stability_measurement,
)
from oled_app.series.paths import ensure_measurement_folder
from oled_app.settings import DEFAULT_APP_SETTINGS, load_app_settings

from .logging_setup import log_directory
from .poc import utc_now


NUMERIC_FIELDS = {
    "current_setpoint_mA": (0.0, 10.0),
    "voltage_setpoint_V": (0.0, 10.0),
    "voltage_start": (0.0, 10.0),
    "voltage_limit": (0.01, 10.0),
    "current_limit_mA": (0.01, 10.0),
    "measurement_time_s": (0.02, 604800.0),
    "sample_interval_s": (0.01, 3600.0),
    "autosave_interval_s": (0.01, 86400.0),
}
REQUEST_FIELDS = set(NUMERIC_FIELDS) | {"control_mode"}


def default_params() -> StabilityParams:
    advanced = DEFAULT_APP_SETTINGS["stability_advanced"]
    units = DEFAULT_APP_SETTINGS["measurement_units"]
    return StabilityParams(
        com_port="SIM",
        control_mode="current",
        current_setpoint_mA=3.5,
        voltage_setpoint_V=3.5,
        voltage_start=3.0,
        voltage_limit=5.0,
        current_limit_mA=10.0,
        voltage_step_max=float(advanced["voltage_step_max"]),
        current_control_kp=float(advanced["current_control_kp"]),
        measurement_time_s=60.0,
        sample_interval_s=1.0,
        autosave_interval_s=60.0,
        photodiode_bias_V=float(advanced["photodiode_bias_V"]),
        photodiode_threshold_uA=float(advanced["photodiode_threshold_uA"]),
        photodiode_range=int(advanced["photodiode_range"]),
        pixel_area_mm2=float(units["pixel_area_mm2"]),
        luminance_cd_m2_per_uA=float(units.get("luminance_green_cd_m2_per_uA", 1.0)),
        geometric_coefficient=float(units["geometric_conversion_coefficient"]),
    )


def public_params(params: StabilityParams) -> Dict[str, Any]:
    return {key: getattr(params, key) for key in REQUEST_FIELDS}


def validate_params(payload: Dict[str, Any]) -> StabilityParams:
    if not isinstance(payload, dict) or set(payload) - REQUEST_FIELDS:
        raise ValueError("Неизвестные параметры стабильности.")
    params = default_params()
    for key, value in payload.items():
        if key == "control_mode":
            if value not in StabilitySetpointController.MODES:
                raise ValueError("Режим стабильности должен быть current или voltage.")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key}: требуется число.")
            low, high = NUMERIC_FIELDS[key]
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{key}: допустимо от {low} до {high}.")
        setattr(params, key, value)
    if params.voltage_start > params.voltage_limit:
        raise ValueError("Стартовое напряжение не должно превышать предел напряжения.")
    if params.voltage_setpoint_V > params.voltage_limit:
        raise ValueError("Уставка напряжения не должна превышать предел напряжения.")
    if params.current_setpoint_mA > params.current_limit_mA:
        raise ValueError("Уставка тока не должна превышать предел тока.")
    if params.sample_interval_s > params.measurement_time_s:
        raise ValueError("Интервал точки не должен превышать длительность измерения.")
    return params


class StabilityController:
    def __init__(self, output_root: Optional[Path] = None, series_service=None):
        self.output_root = (
            Path(output_root) if output_root else log_directory().parent / "simulator_stability"
        )
        self.series_service = series_service
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._control: Optional[StabilitySetpointController] = None
        self._state: Dict[str, Any] = {
            "status": "idle", "active": False, "error": None, "run_id": None,
            "points": [], "point_count": 0, "safe_shutdown_confirmed": None,
            "result": None,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def _prepare(self, payload: Dict[str, Any]):
        if not isinstance(payload, dict):
            raise ValueError("Параметры стабильности должны быть объектом.")
        values = dict(payload)
        target_request = values.pop("target", None)
        use_ivl_start = values.pop("use_ivl_start_voltage", False)
        if not isinstance(use_ivl_start, bool):
            raise ValueError("Режим расчёта по ВАЯХ должен быть логическим значением.")
        params = validate_params(values)
        target = None
        if target_request is not None:
            if self.series_service is None:
                raise ValueError("Стабильность серии недоступна.")
            target = self.series_service.stability_target(
                target_request, params, load_app_settings(), use_ivl_start
            )
        elif use_ivl_start:
            raise ValueError("Автоматический старт по ВАЯХ доступен только для пикселя серии.")
        elif params.control_mode == "voltage":
            params.voltage_start = params.voltage_setpoint_V
        return params, target, use_ivl_start

    def preflight(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params, target, use_ivl_start = self._prepare(payload)
        return {
            "mode": "simulator",
            "params": public_params(params),
            "target": (
                {key: target[key] for key in ("series_path", "pixel_id")} if target else None
            ),
            "use_ivl_start_voltage": use_ivl_start,
            "effective_voltage_start": params.voltage_start,
            "start_voltage_source": target.get("start_voltage_source") if target else "manual",
            "ivl_voltage_at_target": target.get("ivl_voltage_at_target") if target else None,
            "output_root": target["series_path"] if target else str(self.output_root),
            "note": (
                "Эмулятор: результат будет записан в журнал выбранной серии."
                if target else
                "Эмулятор SIM_STABILITY: результат хранится отдельно от серий."
            ),
        }

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params, target, use_ivl_start = self._prepare(payload)
        with self._lock:
            if self._state["active"]:
                raise RuntimeError("Измерение стабильности уже выполняется.")
            maximum = (
                params.current_limit_mA
                if params.control_mode == "current" else params.voltage_limit
            )
            initial_target = (
                params.current_setpoint_mA
                if params.control_mode == "current" else params.voltage_setpoint_V
            )
            self._control = StabilitySetpointController(
                params.control_mode, initial_target, maximum=maximum
            )
            run_id = uuid4().hex
            self._state = {
                "status": "running", "active": True, "error": None,
                "run_id": run_id, "started_at": utc_now(), "finished_at": None,
                "pixel_id": target["pixel_id"] if target else "SIM_STABILITY",
                "target": (
                    {key: target[key] for key in ("series_path", "pixel_id")}
                    if target else None
                ),
                "use_ivl_start_voltage": use_ivl_start,
                "params": public_params(params), "points": [], "point_count": 0,
                "latest_point": None, "current_setpoint": initial_target,
                "setpoint_revision": self._control.snapshot()[1],
                "safe_shutdown_confirmed": None, "result": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(params, target, run_id),
                daemon=True,
                name="oled-v2-stability",
            )
            self._thread.start()
            return self.snapshot()

    def setpoint(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if (not self._state["active"] or payload.get("run_id") != self._state["run_id"]
                    or self._control is None):
                raise RuntimeError("Запуск стабильности завершён или изменился.")
            value = payload.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Уставка должна быть числом.")
            if not math.isfinite(value):
                raise ValueError("Уставка должна быть конечным числом.")
            target = self._control.set_target(value)
            self._state["current_setpoint"] = target
            self._state["setpoint_revision"] = self._control.snapshot()[1]
            self._state["message"] = f"Новая уставка: {target:g}"
            return self.snapshot()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._state["active"] and self._control is not None:
                self._control.request_stop()
                self._state["status"] = "stop_requested"
            return self.snapshot()

    def shutdown(self) -> None:
        self.stop()
        if self._thread:
            self._thread.join(timeout=5)

    def _log(self, message: str) -> None:
        with self._lock:
            self._state["message"] = str(message)

    def _shutdown_state(self, confirmed: bool) -> None:
        with self._lock:
            self._state["safe_shutdown_confirmed"] = bool(confirmed)

    def _point(self, point: Dict[str, Any]) -> None:
        public = {
            "point": int(point["point"]),
            "elapsed_s": float(point["elapsed_s"]),
            "control_mode": str(point["control_mode"]),
            "target_setpoint": float(point["target_setpoint"]),
            "target_unit": str(point["target_unit"]),
            "voltage_set_V": float(point["voltage_set_V"]),
            "voltage_measured_V": float(point["voltage_measured_V"]),
            "current_measured_mA": float(point["current_measured_mA"]),
            "photodiode_uA": float(point["photodiode_uA"]),
            "luminance_cd_m2": float(point["luminance_cd_m2"]),
        }
        with self._lock:
            if self._state["points"] and self._state["points"][-1]["point"] == public["point"]:
                self._state["points"][-1] = public
            else:
                self._state["points"].append(public)
                self._state["points"] = self._state["points"][-1000:]
            self._state["point_count"] = public["point"]
            self._state["latest_point"] = public
            self._state["current_setpoint"] = public["target_setpoint"]

    def _run(self, params: StabilityParams, target, run_id: str) -> None:
        terminal = "failed"
        try:
            private = self.output_root / run_id
            private.mkdir(parents=True, exist_ok=False)
            settings = deepcopy(load_app_settings())
            settings["hardware_mode"] = HARDWARE_MODE_SIM
            settings["simulator_config_path"] = str(private / "simulator_config.json")
            pixel_id = target["pixel_id"] if target else "SIM_STABILITY"
            folder = (
                ensure_measurement_folder(
                    Path(target["series_path"]), "STABILITY", pixel_id, target["pixel_row"]
                )
                if target else private
            )
            with self._lock:
                self._state["output_folder"] = str(folder)
            result = run_stability_measurement(
                pixel_id,
                folder,
                params,
                self._log,
                settings,
                control=self._control,
                progress=self._point,
                file_suffix=f"{run_id[:8]}_{uuid4().hex[:6]}",
                shutdown_callback=self._shutdown_state,
            )
            public = {
                "file": str(result["file"]),
                "raw_file": str(result["raw_file"]) if result.get("raw_file") else None,
                "status": result["status"],
                "max_photo_uA": result["max_photo_uA"],
                "control_mode": result["control_mode"],
                "final_setpoint": result["final_setpoint"],
                "stopped_by_user": bool(result["stopped_by_user"]),
                "events": deepcopy(result.get("events") or []),
                "journaled": False,
                "run_id": run_id,
            }
            if target:
                self.series_service.record_stability(target, params, public)
                public["journaled"] = True
            with self._lock:
                self._state["result"] = deepcopy(public)
            terminal = "stopped" if public["stopped_by_user"] else "completed"
        except Exception as exc:
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state.update(status=terminal, active=False, finished_at=utc_now())
