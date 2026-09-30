"""Backend-owned single-pixel spectrum sessions for the v2 simulator."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import threading
from typing import Any, Dict, Optional
from uuid import uuid4

import numpy as np

from oled_app.constants import HARDWARE_MODE_SIM
from oled_app.measurements.spectrum import (
    SpectrumMeasurementController,
    SpectrumMeasurementStopped,
    SpectrumParams,
    discard_spectrum_artifacts,
    run_spectrum_measurement,
    save_rejected_spectrum_workbook,
)
from oled_app.series.paths import ensure_measurement_folder
from oled_app.settings import load_app_settings

from .logging_setup import log_directory
from .poc import utc_now


NUMERIC_FIELDS = {
    "voltage_start": (0.0, 10.0),
    "voltage_end": (0.0, 10.0),
    "voltage_step": (0.001, 2.0),
    "current_limit_mA": (0.01, 10.0),
    "target_intensity": (100.0, 65000.0),
    "intensity_min": (0.0, 65000.0),
    "intensity_max": (100.0, 65535.0),
    "saturation_level": (100.0, 65535.0),
    "t_int_initial_s": (0.000001, 30.0),
    "t_int_min_s": (0.000001, 30.0),
    "t_int_max_s": (0.000001, 30.0),
    "max_iterations": (1, 100),
    "settle_time_voltage_s": (0.0, 5.0),
    "settle_time_spectrum_s": (0.0, 5.0),
}
BOOLEAN_FIELDS = {
    "reuse_previous_integration_time",
    "discard_first_scan_after_tint_change",
    "baseline_correction_enabled",
    "peak_detection_enabled",
}
LED_TYPES = {"auto", "red", "green", "blue", "white", "other", "visible", "all"}
REQUEST_FIELDS = set(NUMERIC_FIELDS) | BOOLEAN_FIELDS | {"led_type"}


def default_params() -> SpectrumParams:
    settings = load_app_settings()
    advanced = settings["spectrum_advanced"]
    units = settings["measurement_units"]
    return SpectrumParams(
        com_port="SIM",
        voltage_start=2.0,
        voltage_end=5.0,
        voltage_step=0.1,
        current_limit_mA=6.0,
        photodiode_bias_V=float(advanced["photodiode_bias_V"]),
        photodiode_range=int(advanced["photodiode_range"]),
        target_intensity=float(advanced["target_intensity"]),
        intensity_min=float(advanced["intensity_min"]),
        intensity_max=float(advanced["intensity_max"]),
        saturation_level=float(advanced["saturation_level"]),
        min_peak_width_nm=float(advanced["min_peak_width_nm"]),
        t_int_initial_s=float(advanced["t_int_initial_s"]),
        t_int_min_s=float(advanced["t_int_min_s"]),
        t_int_max_s=float(advanced["t_int_max_s"]),
        reuse_previous_integration_time=bool(advanced["reuse_previous_integration_time"]),
        discard_first_scan_after_tint_change=bool(advanced["discard_first_scan_after_tint_change"]),
        kp=float(advanced["kp"]),
        ki=float(advanced["ki"]),
        max_iterations=int(advanced["max_iterations"]),
        tolerance=float(advanced["tolerance"]),
        led_type="auto",
        peak_search_mode_for_tint=str(advanced["peak_search_mode_for_tint"]),
        settle_time_voltage_s=float(advanced["settle_time_voltage_s"]),
        settle_time_spectrum_s=float(advanced["settle_time_spectrum_s"]),
        dark_spectrum_enabled=bool(advanced["dark_spectrum_enabled"]),
        dark_spectrum_scans=int(advanced["dark_spectrum_scans"]),
        baseline_correction_enabled=bool(advanced["baseline_correction_enabled"]),
        peak_detection_enabled=bool(advanced["peak_detection_enabled"]),
        pixel_area_mm2=float(units["pixel_area_mm2"]),
        geometric_coefficient=float(units["geometric_conversion_coefficient"]),
    )


def public_params(params: SpectrumParams) -> Dict[str, Any]:
    return {key: getattr(params, key) for key in REQUEST_FIELDS}


def validate_params(payload: Dict[str, Any]) -> SpectrumParams:
    if not isinstance(payload, dict) or set(payload) - REQUEST_FIELDS:
        raise ValueError("Неизвестные параметры спектрального измерения.")
    params = default_params()
    for key, value in payload.items():
        if key in BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{key}: требуется логическое значение.")
        elif key == "led_type":
            if not isinstance(value, str) or value not in LED_TYPES:
                raise ValueError("Тип LED должен быть auto, red, green, blue, white, other, visible или all.")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key}: требуется число.")
            low, high = NUMERIC_FIELDS[key]
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{key}: допустимо от {low} до {high}.")
            if key == "max_iterations":
                if int(value) != value:
                    raise ValueError("max_iterations: требуется целое число.")
                value = int(value)
        setattr(params, key, value)
    if params.voltage_end < params.voltage_start:
        raise ValueError("Конечное напряжение не должно быть меньше начального.")
    point_count = int(round((params.voltage_end - params.voltage_start) / params.voltage_step)) + 1
    if point_count > 500:
        raise ValueError("В одном спектральном запуске допускается не более 500 точек.")
    if not params.intensity_min <= params.target_intensity <= params.intensity_max:
        raise ValueError("Целевая интенсивность должна лежать между нижней и верхней границей.")
    if params.intensity_max > params.saturation_level:
        raise ValueError("Верхняя граница интенсивности не должна превышать уровень насыщения.")
    if not params.t_int_min_s <= params.t_int_initial_s <= params.t_int_max_s:
        raise ValueError("Начальное время интегрирования должно лежать между минимумом и максимумом.")
    return params


class SpectrumController:
    def __init__(self, output_root: Optional[Path] = None, series_service=None):
        self.output_root = Path(output_root) if output_root else log_directory().parent / "simulator_spectrum"
        self.series_service = series_service
        self._lock = threading.RLock()
        self._decision_ready = threading.Event()
        self._decision_value: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None
        self._control = SpectrumMeasurementController()
        self._state: Dict[str, Any] = {
            "status": "idle", "active": False, "error": None, "run_id": None,
            "points": [], "latest_spectrum": None, "optimization": None,
            "safe_shutdown_confirmed": None, "result": None,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def _prepare(self, payload: Dict[str, Any]):
        if not isinstance(payload, dict):
            raise ValueError("Параметры спектра должны быть объектом.")
        values = dict(payload)
        target = values.pop("target", None)
        queue_request = values.pop("queue", None)
        use_opening = values.pop("use_opening_voltage", True)
        if target is not None and queue_request is not None:
            raise ValueError("Выберите одиночный пиксель или спектральную очередь.")
        if not isinstance(use_opening, bool):
            raise ValueError("Режим напряжения открытия должен быть логическим значением.")
        params = validate_params(values)
        context = None
        queue = None
        if target is not None:
            if self.series_service is None:
                raise ValueError("Спектральное измерение серии недоступно.")
            context = self.series_service.spectrum_target(
                target, params, load_app_settings(), use_opening
            )
        elif queue_request is not None:
            if self.series_service is None:
                raise ValueError("Спектральная очередь серии недоступна.")
            queue = self.series_service.spectrum_queue_targets(queue_request)
            first_params = deepcopy(params)
            first = queue["targets"][0]
            self.series_service.spectrum_target(
                {"series_path": first["series_path"], "pixel_id": first["pixel_id"]},
                first_params, load_app_settings(), use_opening,
            )
            if first_params.voltage_start > first_params.voltage_end:
                raise ValueError("Напряжение открытия стартового пикселя выше конечного напряжения.")
        else:
            params.opening_voltage = params.voltage_start
            params.voltage_start_source = "manual"
        if params.voltage_start > params.voltage_end:
            raise ValueError(
                "Напряжение открытия выше конечного напряжения измерения."
            )
        return params, context, queue, use_opening

    def preflight(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params, target, queue, use_opening = self._prepare(payload)
        effective = deepcopy(params)
        if queue:
            first = queue["targets"][0]
            self.series_service.spectrum_target(
                {"series_path": first["series_path"], "pixel_id": first["pixel_id"]},
                effective, load_app_settings(), use_opening,
            )
        points = int(round((effective.voltage_end - effective.voltage_start) / effective.voltage_step)) + 1
        return {
            "mode": "simulator",
            "params": public_params(params),
            "target": {key: target[key] for key in ("series_path", "pixel_id")} if target else None,
            "queue": self._queue_public(queue) if queue else None,
            "use_opening_voltage": use_opening,
            "effective_voltage_start": effective.voltage_start,
            "point_count": points,
            "output_root": (target or queue or {}).get("series_path", str(self.output_root)),
            "note": (
                f"Эмулятор: очередь из {len(queue['targets'])} пикселей будет записана в журнал серии."
                if queue else "Эмулятор: результат будет записан в журнал выбранной серии."
                if target else "Эмулятор SIM_SPECTRUM: результат хранится отдельно от серий."
            ),
        }

    @staticmethod
    def _queue_public(queue):
        return {
            "enabled": True,
            "series_path": queue["series_path"],
            "start_pixel": queue["start_pixel"],
            "scope": queue["scope"],
            "queued_only": queue["queued_only"],
            "candidate_count": queue["candidate_count"],
            "total": len(queue["targets"]),
        }

    @classmethod
    def _queue_state(cls, queue):
        return {
            **cls._queue_public(queue),
            "completed": 0,
            "remaining": len(queue["targets"]),
            "attempts": 0,
            "current_index": 0,
            "current_pixel": queue["targets"][0]["pixel_id"],
            "completed_pixels": [],
            "skipped_pixels": [],
            "results": [],
        }

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params, target, queue, use_opening = self._prepare(payload)
        with self._lock:
            if self._state["active"]:
                raise RuntimeError("Спектральное измерение уже выполняется.")
            self._control = SpectrumMeasurementController()
            self._decision_ready.clear()
            self._decision_value = None
            run_id = uuid4().hex
            pixel_id = target["pixel_id"] if target else queue["targets"][0]["pixel_id"] if queue else "SIM_SPECTRUM"
            self._state = {
                "status": "running", "active": True, "error": None, "run_id": run_id,
                "started_at": utc_now(), "finished_at": None, "pixel_id": pixel_id,
                "target": {key: target[key] for key in ("series_path", "pixel_id")} if target else None,
                "use_opening_voltage": use_opening, "params": public_params(params),
                "points": [], "point_count": 0, "latest_spectrum": None,
                "optimization": None, "message": "Подготовка спектрометра…",
                "safe_shutdown_confirmed": None, "result": None,
                "decision": None,
                "queue": self._queue_state(queue) if queue else None,
            }
            self._thread = threading.Thread(
                target=self._run, args=(params, target, queue, run_id), daemon=True,
                name="oled-v2-spectrum",
            )
            self._thread.start()
            return self.snapshot()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._state["active"]:
                self._control.request_stop()
                self._decision_ready.set()
                self._state["status"] = "stop_requested"
                self._state["message"] = "Запрошена безопасная остановка…"
            return self.snapshot()

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            decision = self._state.get("decision")
            if (not decision or payload.get("run_id") != self._state.get("run_id")
                    or payload.get("decision_id") != decision.get("id")):
                raise RuntimeError("Запрос решения устарел или уже обработан.")
            action = payload.get("action")
            if action not in decision.get("actions", []):
                raise ValueError("Недопустимое действие для текущего решения.")
            pixel_id = payload.get("pixel_id")
            if action == "replace" and pixel_id not in decision.get("replacement_pixels", []):
                raise ValueError("Выбранный пиксель замены недоступен.")
            self._decision_value = {"action": action, "pixel_id": pixel_id}
            self._state["decision"] = None
            self._state["status"] = "processing"
            self._decision_ready.set()
            return self.snapshot()

    def shutdown(self) -> None:
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=8)

    def _log(self, message: str) -> None:
        with self._lock:
            self._state["message"] = str(message).strip()

    @staticmethod
    def _curve(wavelengths, values) -> Dict[str, list[float]]:
        x = np.asarray(wavelengths, dtype=float)
        y = np.asarray(values, dtype=float)
        stride = max(1, int(math.ceil(len(x) / 260)))
        return {
            "wavelengths_nm": [round(float(value), 3) for value in x[::stride]],
            "intensities": [round(float(value), 3) for value in y[::stride]],
        }

    def _optimization_preview(self, point, voltage, iteration, integration_time, wavelengths, intensities, status):
        with self._lock:
            self._state["optimization"] = {
                "point": int(point), "voltage_V": float(voltage), "iteration": int(iteration),
                "integration_time_s": float(integration_time), "status": str(status),
                **self._curve(wavelengths, intensities),
            }

    def _point(self, index, voltage, integration_time, wavelengths, raw, processed, peaks, status):
        raw_values = np.asarray(raw, dtype=float)
        peak_index = int(np.nanargmax(raw_values)) if raw_values.size else 0
        point = {
            "index": int(index), "voltage_V": float(voltage),
            "integration_time_s": float(integration_time), "status": str(status),
            "peak_nm": float(np.asarray(wavelengths)[peak_index]) if raw_values.size else None,
            "peak_counts": float(raw_values[peak_index]) if raw_values.size else None,
            "peaks_detected": len(peaks),
        }
        with self._lock:
            self._state["points"].append(point)
            self._state["point_count"] = len(self._state["points"])
            self._state["latest_spectrum"] = {
                "point": int(index), "voltage_V": float(voltage), "status": str(status),
                "integration_time_s": float(integration_time),
                **self._curve(wavelengths, processed),
            }
            self._state["optimization"] = None

    def _await_decision(self, kind: str, message: str, actions, **extra) -> Dict[str, Any]:
        with self._lock:
            self._decision_ready.clear()
            self._decision_value = None
            self._state["status"] = f"awaiting_{kind}"
            self._state["decision"] = {
                "id": uuid4().hex,
                "kind": kind,
                "message": message,
                "actions": list(actions),
                **extra,
            }
        while not self._decision_ready.wait(0.1):
            if self._control.stop_requested():
                raise SpectrumMeasurementStopped()
        if self._control.stop_requested() or not self._decision_value:
            raise SpectrumMeasurementStopped()
        if self._decision_value["action"] == "stop":
            raise SpectrumMeasurementStopped()
        return self._decision_value

    def _run(self, params: SpectrumParams, target, queue, run_id: str) -> None:
        terminal = "failed"
        try:
            private = self.output_root / run_id
            private.mkdir(parents=True, exist_ok=False)
            settings = deepcopy(load_app_settings())
            settings["hardware_mode"] = HARDWARE_MODE_SIM
            settings["simulator_config_path"] = str(private / "simulator_config.json")
            if queue:
                self._run_queue(params, queue, settings, private, run_id)
            else:
                result = self._measure_one(
                    params, target, settings, private, run_id,
                    interactive_rejection=False,
                )
                if result["stopped_by_user"]:
                    raise SpectrumMeasurementStopped()
            terminal = "completed"
        except SpectrumMeasurementStopped:
            terminal = "stopped"
            self._log("Спектральная очередь остановлена. Завершённые результаты сохранены.")
        except Exception as exc:
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state.update(
                    status=terminal, active=False, decision=None, finished_at=utc_now()
                )

    def _run_queue(self, base_params, queue, settings, private, run_id):
        targets = list(queue["targets"])
        attempted = set()
        index = 0
        prompt_next = True
        while index < len(targets):
            if self._control.stop_requested():
                raise SpectrumMeasurementStopped()
            target = targets[index]
            pixel_id = target["pixel_id"]
            pixel_params = deepcopy(base_params)
            target = self.series_service.spectrum_target(
                {"series_path": target["series_path"], "pixel_id": pixel_id},
                pixel_params, settings, self._state["use_opening_voltage"],
            )
            with self._lock:
                self._state.update(
                    pixel_id=pixel_id,
                    target={key: target[key] for key in ("series_path", "pixel_id")},
                    params=public_params(pixel_params), points=[], point_count=0,
                    latest_spectrum=None, optimization=None, result=None,
                    safe_shutdown_confirmed=None,
                )
                self._state["queue"].update(
                    current_index=index, current_pixel=pixel_id,
                    remaining=len(targets) - index,
                )
            if prompt_next:
                next_action = self._await_decision(
                    "next_pixel",
                    f"Установите пиксель {pixel_id} и подтвердите начало съёмки.",
                    ["measure", "skip", "stop"],
                    pixel_id=pixel_id,
                )["action"]
                if next_action == "skip":
                    with self._lock:
                        self._state["queue"]["skipped_pixels"].append(pixel_id)
                        self._state["queue"]["remaining"] = len(targets) - index - 1
                    index += 1
                    continue
            prompt_next = True
            result = self._measure_one(
                pixel_params, target, settings, private, run_id,
                interactive_rejection=True,
            )
            if result["stopped_by_user"]:
                raise SpectrumMeasurementStopped()
            attempted.add(pixel_id)
            with self._lock:
                state = self._state["queue"]
                state["attempts"] += 1
                state["results"].append({
                    "pixel_id": pixel_id, "status": result["status"],
                    "file": result["file"], "journaled": result["journaled"],
                })

            if result["status"] == "NO_CONTACT":
                action = self._await_decision(
                    "no_contact",
                    f"Для {pixel_id} нет контакта. Проверьте установку пикселя.",
                    ["retry", "continue", "stop"],
                    pixel_id=pixel_id,
                )["action"]
                if action == "retry":
                    prompt_next = False
                    continue

            replacement = None
            if result["discarded"] and result["status"] != "NO_CONTACT":
                candidates = self.series_service.spectrum_replacement_targets(
                    target, attempted | set(self._state["queue"]["completed_pixels"])
                )
                if candidates:
                    replacement_ids = [item["pixel_id"] for item in candidates]
                    decision = self._await_decision(
                        "replacement",
                        f"Спектр {pixel_id} отклонён. Можно выбрать замену в той же четверти.",
                        ["replace", "continue", "stop"],
                        pixel_id=pixel_id,
                        replacement_pixels=replacement_ids,
                    )
                    if decision["action"] == "replace":
                        replacement = next(
                            item for item in candidates if item["pixel_id"] == decision["pixel_id"]
                        )

            with self._lock:
                state = self._state["queue"]
                if pixel_id not in state["completed_pixels"]:
                    state["completed_pixels"].append(pixel_id)
                state["completed"] = len(state["completed_pixels"])
            if replacement:
                targets = [item for item in targets if item["pixel_id"] != replacement["pixel_id"]]
                targets.insert(index + 1, replacement)
            with self._lock:
                self._state["queue"]["total"] = len(targets)
                self._state["queue"]["remaining"] = len(targets) - index - 1
            index += 1
        with self._lock:
            self._state["queue"].update(current_pixel=None, remaining=0)
        self._log("Спектральная очередь серии завершена.")

    def _measure_one(
        self, params, target, settings, private, run_id,
        interactive_rejection: bool,
    ) -> Dict[str, Any]:
        pixel_id = target["pixel_id"] if target else "SIM_SPECTRUM"
        folder = (
            ensure_measurement_folder(
                Path(target["series_path"]), "SPECTRUM", pixel_id, target["pixel_row"]
            )
            if target else private
        )
        with self._lock:
            self._state["status"] = "running"
        raw_result = run_spectrum_measurement(
            pixel_id, folder, params, self._log, settings,
            progress_callback=self._point,
            optimization_preview_callback=self._optimization_preview,
            control=self._control,
            file_suffix=f"{run_id[:8]}_{uuid4().hex[:6]}",
        )
        confirmed = raw_result.get("safe_shutdown_confirmed")
        with self._lock:
            self._state["safe_shutdown_confirmed"] = confirmed
        if confirmed is not True:
            raise RuntimeError(
                "Отключение выходов SMU не подтверждено; запись в журнал отменена."
            )
        if raw_result.get("discarded"):
            if raw_result.get("status") == "NO_CONTACT":
                discard_spectrum_artifacts(raw_result.get("raw_files", []), self._log)
                raw_result["raw_files"] = []
            else:
                action = "keep"
                if interactive_rejection:
                    action = self._await_decision(
                        "rejected_data",
                        f"{pixel_id}: сохранить частичные данные как диагностический XLSX?",
                        ["keep", "delete", "stop"],
                        pixel_id=pixel_id,
                        status=raw_result.get("status"),
                    )["action"]
                if action == "keep":
                    raw_result["file"] = save_rejected_spectrum_workbook(
                        pixel_id, params, raw_result
                    )
                else:
                    discard_spectrum_artifacts(raw_result.get("raw_files", []), self._log)
                    raw_result["raw_files"] = []
        public_result = {
            "file": str(raw_result["file"]) if raw_result.get("file") else None,
            "raw_files": [str(path) for path in raw_result.get("raw_files", [])],
            "status": raw_result.get("status"),
            "stopped_by_user": bool(raw_result.get("stopped_by_user")),
            "discarded": bool(raw_result.get("discarded")),
            "spectrum_peak_count": raw_result.get("spectrum_peak_count"),
            "spectrum_peaks_nm": raw_result.get("spectrum_peaks_nm", ""),
            "spectrum_max_intensity": raw_result.get("spectrum_max_intensity"),
            "journaled": False,
            "run_id": run_id,
        }
        if target and not public_result["stopped_by_user"]:
            self.series_service.record_spectrum(target, params, public_result)
            public_result["journaled"] = True
        with self._lock:
            self._state["result"] = deepcopy(public_result)
        return public_result
