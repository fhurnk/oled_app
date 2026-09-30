"""Simulator IVL sessions: cycles, selected series pixel and operator decisions."""

from copy import deepcopy
from pathlib import Path
import math
import threading
import time
from uuid import uuid4

from oled_app.constants import HARDWARE_MODE_SIM
from oled_app.hardware import prepare_hardware_environment, safe_shutdown_smu
from oled_app.measurements.ivl import IVLParams, MeasurementStopped, run_ivl_cycle
from oled_app.measurements.raw_io import RawCsvWriter, raw_csv_path
from oled_app.series.paths import ensure_measurement_folder
from oled_app.processing.ivl_preview import create_ivl_thumbnail, ivl_thumbnail_path
from oled_app.utils import safe_filename, timestamp_for_file
from oled_app.processing.ivl_results import (
    IVL_RAW_HEADERS, build_ivl_workbook_from_raw_csv, final_ivl_status,
    describe_ivl_first_measurement,
)
from oled_app.settings import load_app_settings
from .logging_setup import log_directory
from .poc import utc_now


FIELDS = {
    "sweep_start": (0, 10), "sweep_end": (0, 10),
    "sweep_increment": (0.001, 1), "sweep_time_per_point": (0.01, 0.5),
    "current_limit_mA": (0.01, 10), "pixel_area_mm2": (0.001, 1000),
    "photodiode_threshold_uA": (0, 1000),
    "opening_photodiode_threshold_uA": (0, 1000),
    "working_confirmation_points": (0, 100), "opening_confirmation_points": (0, 100),
    "num_cycles": (1, 10), "delay_between_cycles": (0, 5),
    "burned_confirmation_cycles": (0, 3), "burnout_current_threshold_mA": (0.01, 10),
}


def default_params():
    settings = load_app_settings()
    advanced = settings["ivl_advanced"]
    units = settings["measurement_units"]
    return IVLParams(
        com_port="SIM",
        photodiode_bias_V=float(advanced["photodiode_bias_V"]),
        photodiode_range=int(advanced["photodiode_range"]),
        photodiode_threshold_uA=float(advanced["photodiode_threshold_uA"]),
        working_confirmation_points=int(advanced["working_confirmation_points"]),
        opening_photodiode_threshold_uA=float(advanced["opening_photodiode_threshold_uA"]),
        opening_confirmation_points=int(advanced["opening_confirmation_points"]),
        burnout_current_threshold_mA=float(advanced["burnout_current_threshold_mA"]),
        mark_current_limit_as_burnout=bool(advanced["mark_current_limit_as_burnout"]),
        no_contact_max_led_current_mA=float(advanced["no_contact_max_led_current_mA"]),
        burned_confirmation_cycles=int(advanced["burned_confirmation_cycles"]),
        pixel_area_mm2=float(units["pixel_area_mm2"]),
        luminance_cd_m2_per_uA=float(units.get("luminance_green_cd_m2_per_uA", 1.0)),
        geometric_coefficient=float(units["geometric_conversion_coefficient"]),
    )


def validate_params(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS.keys():
        raise ValueError("Неизвестные параметры ВАЯХ.")
    params = default_params()
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key}: требуется число.")
        low, high = FIELDS[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key}: допустимо от {low} до {high}.")
        if key.endswith("confirmation_points") or key in {"num_cycles", "burned_confirmation_cycles"}:
            if int(value) != value:
                raise ValueError(f"{key}: требуется целое число.")
            value = int(value)
        setattr(params, key, value)
    if params.sweep_end <= params.sweep_start:
        raise ValueError("Конечное напряжение должно быть больше начального.")
    if (params.sweep_end - params.sweep_start) / params.sweep_increment > 1999:
        raise ValueError("В одном цикле допускается не более 2000 точек.")
    return params


class IvlController:
    def __init__(self, output_root=None, series_service=None):
        self.output_root = Path(output_root) if output_root else log_directory().parent / "simulator_ivl"
        self.series_service = series_service
        self._lock = threading.RLock()
        self._decision_ready = threading.Event()
        self._decision_value = None
        self._stop = threading.Event()
        self._thread = None
        self._state = {"status": "idle", "active": False, "points": [], "error": None,
                       "safe_shutdown_confirmed": None, "result": None, "run_id": None}

    def snapshot(self):
        with self._lock:
            return deepcopy(self._state)

    def _prepare(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Параметры ВАЯХ должны быть объектом.")
        values = dict(payload)
        target = values.pop("target", None)
        queue_request = values.pop("queue", None)
        if target is not None and queue_request is not None:
            raise ValueError("Выберите одиночный пиксель или очередь серии.")
        params = validate_params(values)
        context = None
        queue = None
        if target is not None:
            if self.series_service is None:
                raise ValueError("Серийная ВАЯХ недоступна.")
            context = self.series_service.ivl_target(target, params, load_app_settings())
        elif queue_request is not None:
            if self.series_service is None:
                raise ValueError("Очередь серии недоступна.")
            queue = self.series_service.ivl_queue_targets(queue_request)
        return params, context, queue

    def preflight(self, payload):
        params, target, queue = self._prepare(payload)
        return {"mode": "simulator", "params": {k: getattr(params, k) for k in FIELDS},
                "target": {k: target[k] for k in ("series_path", "pixel_id")} if target else None,
                "queue": self._queue_public(queue) if queue else None,
                "output_root": (target or queue or {}).get("series_path", str(self.output_root)),
                "cycles": params.num_cycles,
                "luminance_coefficient": params.luminance_cd_m2_per_uA,
                "spectral_calibration": (params.luminance_calibration_model is not None
                                         if target else None if queue else False),
                "note": (f"Эмулятор: очередь из {len(queue['targets'])} пикселей будет записана в журнал серии."
                         if queue else "Эмулятор: результат будет записан в журнал выбранной серии."
                         if target else "Эмулятор SIM_IVL: результаты отделены от серий.")}

    def start(self, payload):
        params, target, queue = self._prepare(payload)
        with self._lock:
            if self._state["active"]:
                raise RuntimeError("ВАЯХ уже выполняется.")
            self._stop.clear()
            self._decision_ready.clear()
            self._state = {"status": "running", "active": True, "points": [], "error": None,
                           "safe_shutdown_confirmed": None, "result": None,
                           "run_id": uuid4().hex, "started_at": utc_now(),
                           "pixel_id": (target["pixel_id"] if target else
                                        queue["targets"][0]["pixel_id"] if queue else "SIM_IVL"),
                           "target": {k: target[k] for k in ("series_path", "pixel_id")} if target else None,
                           "cycle": 1, "point_count": 0, "decision": None,
                           "params": {k: getattr(params, k) for k in FIELDS},
                           "queue": self._queue_state(queue) if queue else None}
            self._thread = threading.Thread(target=self._run, args=(params, target, queue), daemon=True,
                                            name="oled-v2-ivl")
            self._thread.start()
            return self.snapshot()

    @staticmethod
    def _queue_public(queue):
        return {
            "enabled": True,
            "series_path": queue["series_path"],
            "start_pixel": queue["start_pixel"],
            "skip_nonworking": queue["skip_nonworking"],
            "candidate_count": queue["candidate_count"],
            "total": len(queue["targets"]),
            "skipped_pixels": list(queue["skipped_pixels"]),
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
            "results": [],
        }

    def decide_opening(self, payload):
        with self._lock:
            decision = self._state.get("decision")
            if (not decision or self._state["status"] != "awaiting_opening"
                    or payload.get("run_id") != self._state["run_id"]
                    or payload.get("decision_id") != decision["id"]):
                raise RuntimeError("Запрос решения устарел или уже обработан.")
            if "value" not in payload:
                raise ValueError("Укажите напряжение или null для пропуска.")
            value = payload["value"]
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 10):
                raise ValueError("Напряжение открытия должно быть от 0 до 10 В.")
            self._decision_value = value
            self._state["decision"] = None
            self._state["status"] = "processing"
            self._decision_ready.set()
            return self.snapshot()

    def decide_queue(self, payload):
        with self._lock:
            decision = self._state.get("decision")
            if (not decision or decision.get("kind") != "queue_no_contact"
                    or self._state["status"] != "awaiting_queue_decision"
                    or payload.get("run_id") != self._state["run_id"]
                    or payload.get("decision_id") != decision["id"]):
                raise RuntimeError("Запрос решения устарел или уже обработан.")
            action = payload.get("action")
            if action not in {"retry", "skip_substrate", "continue"}:
                raise ValueError("Действие должно быть retry, skip_substrate или continue.")
            self._decision_value = action
            self._state["decision"] = None
            self._state["status"] = "processing"
            self._decision_ready.set()
            return self.snapshot()

    def _opening_voltage(self, result):
        with self._lock:
            self._decision_ready.clear()
            self._state["status"] = "awaiting_opening"
            self._state["decision"] = {"id": uuid4().hex, "kind": "opening_voltage",
                "message": "Пиксель WORKING, но напряжение открытия не определено. Введите его или сохраните без значения."}
        while not self._decision_ready.wait(0.1):
            if self._stop.is_set():
                raise MeasurementStopped()
        if self._stop.is_set():
            raise MeasurementStopped()
        result["opening_voltage"] = self._decision_value

    def _queue_no_contact(self, pixel_id):
        with self._lock:
            self._decision_ready.clear()
            self._state["status"] = "awaiting_queue_decision"
            self._state["decision"] = {
                "id": uuid4().hex,
                "kind": "queue_no_contact",
                "message": (
                    f"Для {pixel_id} нет контакта. Повторите пиксель, пропустите остаток "
                    "этой подложки или продолжите обычную очередь."
                ),
                "pixel_id": pixel_id,
                "actions": ["retry", "skip_substrate", "continue"],
            }
        while not self._decision_ready.wait(0.1):
            if self._stop.is_set():
                raise MeasurementStopped()
        if self._stop.is_set():
            raise MeasurementStopped()
        return self._decision_value

    def stop(self):
        with self._lock:
            if self._state["active"] and self._state["status"] != "processing":
                self._stop.set()
                self._state["status"] = "stop_requested"
            return self.snapshot()

    def shutdown(self):
        self.stop()
        if self._thread:
            self._thread.join(timeout=5)

    def _log(self, message):
        with self._lock:
            self._state["message"] = str(message)

    def _point(self, cycle, row):
        with self._lock:
            if self._state["cycle"] != cycle:
                self._state["points"] = []
                self._state["cycle"] = cycle
            self._state["point_count"] += 1
            self._state["points"].append({
                "index": len(self._state["points"]) + 1,
                "elapsed_s": row["Measurement time (s)"],
                "voltage_set_V": row["Voltage set (V)"],
                "voltage_measured_V": row["Voltage OLED / LED measured (V)"],
                "current_mA": row["Current OLED / LED (mA)"],
                "photodiode_uA": row["Photodiode current (uA)"],
            })
        if self._stop.is_set():
            raise MeasurementStopped()

    def _run(self, params, target, queue):
        final_status = "failed"
        run_id = self._state["run_id"]
        try:
            private_folder = self.output_root / run_id
            private_folder.mkdir(parents=True, exist_ok=False)
            settings = deepcopy(load_app_settings())
            settings["hardware_mode"] = HARDWARE_MODE_SIM
            settings["simulator_config_path"] = str(private_folder / "simulator_config.json")
            if queue:
                self._run_queue(params, queue, settings, private_folder, run_id)
            else:
                self._measure_one(params, target, settings, private_folder, run_id)
            final_status = "completed"
        except MeasurementStopped:
            final_status = "stopped"
            self._log("Остановлено. Уже записанные результаты сохранены; текущий незавершённый результат в журнал не добавлен.")
        except Exception as exc:
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state.update(status=final_status, active=False, decision=None,
                                   finished_at=utc_now())

    def _run_queue(self, params, queue, settings, private_folder, run_id):
        targets = list(queue["targets"])
        index = 0
        while index < len(targets):
            if self._stop.is_set():
                raise MeasurementStopped()
            target = targets[index]
            pixel_params = deepcopy(params)
            target = self.series_service.ivl_target(
                {"series_path": target["series_path"], "pixel_id": target["pixel_id"]},
                pixel_params,
                settings,
            )
            target["quarter_number"] = int(target["pixel_row"].get("Quarter number") or 0)
            target["substrate_number"] = int(target["pixel_row"].get("Substrate number") or 0)
            with self._lock:
                queue_state = self._state["queue"]
                queue_state.update(
                    current_index=index,
                    current_pixel=target["pixel_id"],
                    remaining=len(targets) - index,
                )
                self._state.update(
                    status="running",
                    pixel_id=target["pixel_id"],
                    target={k: target[k] for k in ("series_path", "pixel_id")},
                    points=[], cycle=1, result=None, safe_shutdown_confirmed=None,
                    params={k: getattr(pixel_params, k) for k in FIELDS},
                )
            result = self._measure_one(pixel_params, target, settings, private_folder, run_id)
            with self._lock:
                queue_state = self._state["queue"]
                queue_state["attempts"] += 1
                queue_state["results"].append({
                    "pixel_id": target["pixel_id"],
                    "status": result["status"],
                    "file": result["file"],
                    "journaled": result["journaled"],
                })

            action = "continue"
            if result["status"] == "NO_CONTACT":
                action = self._queue_no_contact(target["pixel_id"])
            if action == "retry":
                self._log(f"Очередь ВАЯХ: повтор {target['pixel_id']} после проверки контакта.")
                continue

            if action == "skip_substrate":
                same_substrate = lambda item: (
                    int(item.get("quarter_number") or item["pixel_row"].get("Quarter number") or 0)
                    == target["quarter_number"]
                    and int(item.get("substrate_number") or item["pixel_row"].get("Substrate number") or 0)
                    == target["substrate_number"]
                )
                skipped = [item["pixel_id"] for item in targets[index + 1:] if same_substrate(item)]
                targets = targets[:index + 1] + [
                    item for item in targets[index + 1:] if not same_substrate(item)
                ]
                with self._lock:
                    self._state["queue"]["skipped_pixels"].extend(skipped)
                self._log(
                    f"Очередь ВАЯХ: после {target['pixel_id']} пропущено пикселей подложки: {len(skipped)}."
                )

            with self._lock:
                queue_state = self._state["queue"]
                if target["pixel_id"] not in queue_state["completed_pixels"]:
                    queue_state["completed_pixels"].append(target["pixel_id"])
                queue_state["completed"] = len(queue_state["completed_pixels"])
                queue_state["remaining"] = len(targets) - index - 1
            index += 1
        with self._lock:
            self._state["queue"]["current_pixel"] = None
            self._state["queue"]["remaining"] = 0
        self._log("Очередь ВАЯХ серии завершена.")

    def _measure_one(self, params, target, settings, private_folder, run_id):
        pixel_id = target["pixel_id"] if target else "SIM_IVL"
        if target:
            folder = ensure_measurement_folder(
                Path(target["series_path"]), "IVL", pixel_id, target["pixel_row"]
            )
            stem = (
                f"IVL_{safe_filename(pixel_id)}_{timestamp_for_file()}_"
                f"{run_id[:8]}_{uuid4().hex[:6]}"
            )
            raw = raw_csv_path(folder, stem + "_raw.csv", settings)
        else:
            folder, stem = private_folder, "IVL_SIM_IVL"
            raw = folder / (stem + "_raw.csv")
        prepare_hardware_environment(pixel_id, settings, self._log)
        import xtralien
        with self._lock:
            self._state["output_folder"] = str(folder)
            self._state["raw_file"] = str(raw)
        cycles = []
        cycles_to_run = params.num_cycles
        confirmations_left = params.burned_confirmation_cycles
        started = time.monotonic()
        confirmed = None
        with RawCsvWriter(raw, IVL_RAW_HEADERS) as writer:
            with xtralien.Device("SIM") as smu:
                try:
                    cycle_number = 1
                    while cycle_number <= cycles_to_run:
                        if self._stop.is_set():
                            raise MeasurementStopped()
                        cycle = run_ivl_cycle(
                            smu, pixel_id, cycle_number, params, self._log,
                            progress_callback=self._point, raw_writer=writer,
                            measurement_started_monotonic=started,
                        )
                        cycles.append(cycle)
                        if cycle["status"] == "BURNED" and confirmations_left > 0:
                            confirmations_left -= 1
                            cycles_to_run = max(cycles_to_run, cycle_number + 1)
                            self._log("Проверка пробоя: дополнительный подтверждающий цикл.")
                        elif cycle["status"] in {"BURNED", "NO_CONTACT", "NONWORKING"}:
                            break
                        if (cycle_number < cycles_to_run
                                and self._stop.wait(params.delay_between_cycles)):
                            raise MeasurementStopped()
                        cycle_number += 1
                finally:
                    confirmed = safe_shutdown_smu(smu)
                    with self._lock:
                        self._state["safe_shutdown_confirmed"] = confirmed
        if confirmed is not True:
            raise RuntimeError("Отключение выходов SMU не подтверждено; запись в журнал отменена.")
        if self._stop.is_set():
            raise MeasurementStopped()
        with self._lock:
            self._state["status"] = "processing"
        workbook = build_ivl_workbook_from_raw_csv(
            raw, folder / (stem + ".xlsx"), pixel_id, params, cycles
        )
        result = {
            "file": str(workbook),
            "status": final_ivl_status(cycles),
            "opening_voltage": next(
                (cycle["opening_voltage"] for cycle in cycles
                 if cycle["opening_voltage"] is not None),
                None,
            ),
            "current_limit_reached": any(cycle["current_limit_reached"] for cycle in cycles),
            "ivl_diagnosis": describe_ivl_first_measurement(cycles),
            "max_current_mA": max(cycle["max_current_mA"] for cycle in cycles),
            "max_photo_uA": max(cycle["max_photo_uA"] for cycle in cycles),
            "cycles": len(cycles),
            "run_id": run_id,
            "journaled": False,
        }
        with self._lock:
            self._state["result"] = deepcopy(result)
        if target:
            create_ivl_thumbnail(ivl_thumbnail_path(workbook, pixel_id), cycles)
            if result["status"] == "WORKING" and result["opening_voltage"] is None:
                self._opening_voltage(result)
            self.series_service.record_ivl(target, params, result)
            result["journaled"] = True
        with self._lock:
            self._state["result"] = deepcopy(result)
        return result
