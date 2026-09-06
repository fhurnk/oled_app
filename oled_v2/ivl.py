"""First Stage 5 slice: one isolated simulator IVL cycle using the stable algorithm."""

from copy import deepcopy
from pathlib import Path
import math
import threading
from uuid import uuid4

from oled_app.constants import HARDWARE_MODE_SIM
from oled_app.hardware import prepare_hardware_environment, safe_shutdown_smu
from oled_app.measurements.ivl import IVLParams, MeasurementStopped, run_ivl_cycle
from oled_app.measurements.raw_io import RawCsvWriter
from oled_app.processing.ivl_results import IVL_RAW_HEADERS, build_ivl_workbook_from_raw_csv
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
}


def validate_params(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS.keys():
        raise ValueError("Неизвестные параметры ВАЯХ.")
    params = IVLParams(com_port="SIM", burned_confirmation_cycles=0)
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key}: требуется число.")
        low, high = FIELDS[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key}: допустимо от {low} до {high}.")
        if key.endswith("confirmation_points"):
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
    def __init__(self, output_root=None):
        self.output_root = Path(output_root) if output_root else log_directory().parent / "simulator_ivl"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._state = {"status": "idle", "active": False, "points": [], "error": None,
                       "safe_shutdown_confirmed": None, "result": None, "run_id": None}

    def snapshot(self):
        with self._lock:
            return deepcopy(self._state)

    def preflight(self, payload):
        params = validate_params(payload)
        return {"mode": "simulator", "params": {k: getattr(params, k) for k in FIELDS},
                "output_root": str(self.output_root), "cycles": 1,
                "note": "Один цикл эмулятора SIM_IVL. Результаты отделены от серий; калибровка светимости — 1."}

    def start(self, payload):
        params = validate_params(payload)
        with self._lock:
            if self._state["active"]:
                raise RuntimeError("ВАЯХ уже выполняется.")
            self._stop.clear()
            self._state = {"status": "running", "active": True, "points": [], "error": None,
                           "safe_shutdown_confirmed": None, "result": None,
                           "run_id": uuid4().hex, "started_at": utc_now(),
                           "params": {k: getattr(params, k) for k in FIELDS}}
            self._thread = threading.Thread(target=self._run, args=(params,), daemon=True,
                                            name="oled-v2-ivl")
            self._thread.start()
            return self.snapshot()

    def stop(self):
        with self._lock:
            if self._state["active"]:
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

    def _run(self, params):
        confirmed = None
        final_status = "failed"
        try:
            folder = self.output_root / self._state["run_id"]
            folder.mkdir(parents=True, exist_ok=False)
            raw = folder / "IVL_SIM_IVL_raw.csv"
            settings = deepcopy(load_app_settings())
            settings["hardware_mode"] = HARDWARE_MODE_SIM
            # Use a private simulator config, including in installed/read-only builds.
            settings["simulator_config_path"] = str(folder / "simulator_config.json")
            prepare_hardware_environment("SIM_IVL", settings, self._log)
            import xtralien
            with self._lock:
                self._state["output_folder"] = str(folder)
                self._state["raw_file"] = str(raw)
            with RawCsvWriter(raw, IVL_RAW_HEADERS) as writer:
                with xtralien.Device("SIM") as smu:
                    try:
                        if self._stop.is_set():
                            raise MeasurementStopped()
                        cycle = run_ivl_cycle(smu, "SIM_IVL", 1, params, self._log,
                                              progress_callback=self._point, raw_writer=writer)
                    finally:
                        confirmed = safe_shutdown_smu(smu)
            with self._lock:
                self._state["status"] = "processing"
            workbook = build_ivl_workbook_from_raw_csv(raw, folder / "IVL_SIM_IVL.xlsx",
                                                      "SIM_IVL", params, [cycle])
            with self._lock:
                self._state["result"] = {"file": str(workbook), "status": cycle["status"],
                    "opening_voltage": cycle["opening_voltage"],
                    "current_limit_reached": cycle["current_limit_reached"]}
            final_status = "completed"
        except MeasurementStopped:
            final_status = "stopped"
            self._log("Остановлено. Неполный цикл сохранён только в raw CSV.")
        except Exception as exc:
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._state.update(status=final_status, active=False,
                                   safe_shutdown_confirmed=confirmed, finished_at=utc_now())
