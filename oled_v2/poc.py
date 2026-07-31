"""Stage 2 hardware proof-of-concept coordinator.

The first operation is deliberately simulator-only. It exercises the same
xtralien/seabreeze-compatible layer and SMU shutdown helper used by the stable
Tkinter application without writing measurement files or energizing real
hardware.
"""

from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

import numpy as np

from oled_app.constants import HARDWARE_MODE_SIM
from oled_app.hardware.ossila import safe_shutdown_smu
from oled_app.hardware.probe import probe_hardware
from oled_app.hardware.simulator import prepare_hardware_environment
from oled_app.settings import hardware_mode_label, load_app_settings


POC_ACTIVE_STATES = {"starting", "running", "stop_requested"}
POC_MAX_EVENTS = 600
POC_MAX_POINTS = 160


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PocBusyError(RuntimeError):
    """Raised when a second proof-of-concept operation is requested."""


class PocController:
    """Own one simulator PoC operation and its live event history."""

    def __init__(
        self,
        settings_provider: Callable[[], Dict[str, Any]] = load_app_settings,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.settings_provider = settings_provider
        self.logger = logger or logging.getLogger("oled_v2")
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active_smu = None
        self._event_sequence = 0
        self._events: List[Dict[str, Any]] = []
        self._points: List[Dict[str, Any]] = []
        self._state: Dict[str, Any] = {
            "status": "idle",
            "run_id": None,
            "mode": "simulator",
            "started_at": None,
            "finished_at": None,
            "point_count": 0,
            "latest_point": None,
            "stop_reason": None,
            "error": None,
            "safe_shutdown_confirmed": None,
            "spectrometer_model": None,
        }
        self._last_probe: Optional[Dict[str, Any]] = None

    def _append_event_locked(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._event_sequence += 1
        event = {
            "sequence": self._event_sequence,
            "type": event_type,
            "timestamp": utc_now(),
            **payload,
        }
        self._events.append(event)
        if len(self._events) > POC_MAX_EVENTS:
            self._events = self._events[-POC_MAX_EVENTS:]
        self._condition.notify_all()
        return event

    def _publish_state_locked(self) -> None:
        self._append_event_locked("poc_state", {"state": self._snapshot_locked(include_points=False)})

    def _log(self, message: str) -> None:
        self.logger.info("PoC %s", message)
        with self._condition:
            self._append_event_locked("poc_log", {"message": message})

    def _snapshot_locked(self, include_points: bool) -> Dict[str, Any]:
        state = deepcopy(self._state)
        state["active"] = state["status"] in POC_ACTIVE_STATES
        state["can_start"] = state["status"] not in POC_ACTIVE_STATES
        state["probe"] = deepcopy(self._last_probe)
        state["last_event_sequence"] = self._event_sequence
        if include_points:
            state["points"] = deepcopy(self._points)
        return state

    def snapshot(self, include_points: bool = True) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(include_points=include_points)

    def hardware_summary(self, settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        current_settings = settings or self.settings_provider()
        with self._lock:
            probe = deepcopy(self._last_probe)
            ran_simulator = bool(self._state.get("point_count"))
        if probe:
            smu = "ready" if "OK" in str(probe.get("smu", "")).upper() else "unavailable"
            spectrometer = (
                "ready"
                if "OK" in str(probe.get("spectrometer", "")).upper()
                else "unavailable"
            )
        elif ran_simulator:
            smu = "ready"
            spectrometer = "ready"
        else:
            smu = "not_probed"
            spectrometer = "not_probed"
        return {
            "mode": hardware_mode_label(current_settings),
            "smu": smu,
            "spectrometer": spectrometer,
            "camera": "not_probed",
        }

    def probe_current_hardware(self) -> Dict[str, Any]:
        with self._condition:
            if self._state["status"] in POC_ACTIVE_STATES:
                raise PocBusyError(
                    "Проверка оборудования недоступна во время активного PoC."
                )
        settings = self.settings_provider()
        self._log(f"Безопасная проверка оборудования: {hardware_mode_label(settings)}.")
        result = probe_hardware(settings)
        result = {
            **result,
            "mode": hardware_mode_label(settings),
            "checked_at": utc_now(),
        }
        with self._condition:
            self._last_probe = deepcopy(result)
            self._append_event_locked("poc_probe", {"probe": deepcopy(result)})
            self._publish_state_locked()
        return result

    def start_simulator(self, point_count: int = 32, interval_s: float = 0.08) -> Dict[str, Any]:
        points = max(8, min(int(point_count), 120))
        interval = max(0.005, min(float(interval_s), 0.25))
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                raise PocBusyError("Аппаратный PoC уже выполняется.")
            self._stop_event = threading.Event()
            self._points = []
            self._state = {
                "status": "starting",
                "run_id": str(uuid4()),
                "mode": "simulator",
                "started_at": utc_now(),
                "finished_at": None,
                "point_count": 0,
                "latest_point": None,
                "stop_reason": None,
                "error": None,
                "safe_shutdown_confirmed": None,
                "spectrometer_model": None,
            }
            thread = threading.Thread(
                target=self._run_simulator,
                args=(points, interval),
                name="oled-v2-poc",
                daemon=True,
            )
            self._thread = thread
            self._publish_state_locked()
            thread.start()
            return self._snapshot_locked(include_points=True)

    def request_stop(self, reason: str = "operator") -> Dict[str, Any]:
        with self._condition:
            if self._state["status"] not in POC_ACTIVE_STATES:
                return self._snapshot_locked(include_points=True)
            self._stop_event.set()
            self._state["status"] = "stop_requested"
            self._state["stop_reason"] = str(reason)
            active_smu = self._active_smu
            self._publish_state_locked()
        if active_smu is not None:
            confirmed = safe_shutdown_smu(active_smu)
            with self._condition:
                if confirmed:
                    self._state["safe_shutdown_confirmed"] = True
                self._publish_state_locked()
        return self.snapshot(include_points=True)

    def stop_and_wait(self, reason: str = "operator", timeout_s: float = 4.0) -> Dict[str, Any]:
        self.request_stop(reason)
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout_s)))
        return self.snapshot(include_points=True)

    def shutdown(self) -> None:
        self.stop_and_wait(reason="application_shutdown", timeout_s=5.0)

    def events_after(self, sequence: int, timeout_s: float = 1.0) -> List[Dict[str, Any]]:
        cursor = max(0, int(sequence))
        with self._condition:
            if not any(event["sequence"] > cursor for event in self._events):
                self._condition.wait(timeout=max(0.01, float(timeout_s)))
            return [
                deepcopy(event)
                for event in self._events
                if int(event["sequence"]) > cursor
            ]

    def _record_point(self, point: Dict[str, Any]) -> None:
        with self._condition:
            self._points.append(deepcopy(point))
            if len(self._points) > POC_MAX_POINTS:
                self._points = self._points[-POC_MAX_POINTS:]
            self._state["point_count"] = len(self._points)
            self._state["latest_point"] = deepcopy(point)
            self._append_event_locked("poc_point", {"point": deepcopy(point)})

    def _run_simulator(self, point_count: int, interval_s: float) -> None:
        smu = None
        shutdown_confirmed = False
        started_monotonic = time.monotonic()
        try:
            settings = deepcopy(self.settings_provider())
            settings["hardware_mode"] = HARDWARE_MODE_SIM
            settings["com_port"] = "SIM"
            prepare_hardware_environment("V2_POC_SIM", settings, self._log)

            import seabreeze.spectrometers as sb
            import xtralien

            devices = list(sb.list_devices())
            if not devices:
                raise RuntimeError("Эмулятор спектрометра не найден.")
            spectrometer = sb.Spectrometer(devices[0])
            spectrometer.integration_time_micros(10_000)
            wavelengths = np.asarray(spectrometer.wavelengths(), dtype=np.float64)

            with xtralien.Device("SIM") as smu:
                with self._condition:
                    self._active_smu = smu
                    self._state["status"] = "running"
                    self._state["spectrometer_model"] = str(
                        getattr(spectrometer, "model", devices[0])
                    )
                    self._last_probe = {
                        "level": "ok",
                        "title": "Эмулятор PoC готов",
                        "details": "SIM SMU; OLED-SIM-SPECTROMETER",
                        "smu": "SIM OK",
                        "spectrometer": "SIM OK",
                        "mode": "Эмулятор",
                        "checked_at": utc_now(),
                    }
                    self._publish_state_locked()

                smu.smu1.set.voltage(0.0, response=0)
                smu.smu2.set.voltage(-5.0, response=0)
                smu.smu1.set.enabled(True, response=0)
                smu.smu2.set.enabled(True, response=0)
                self._log("Эмулятор SMU и спектрометра запущен; начинается короткий sweep.")

                voltages = np.linspace(0.0, 3.6, point_count, dtype=np.float64)
                for index, voltage in enumerate(voltages, start=1):
                    if self._stop_event.is_set():
                        break
                    smu.smu1.set.voltage(float(voltage), response=0)
                    if self._stop_event.wait(interval_s):
                        break
                    measured_voltage, led_current_A = smu.smu1.measure()[0]
                    _pd_voltage, photodiode_current_A = smu.smu2.measure()[0]
                    intensities = np.asarray(spectrometer.intensities(), dtype=np.float64)
                    peak_index = int(np.argmax(intensities))
                    led_current_mA = float(led_current_A) * 1000.0
                    point = {
                        "index": index,
                        "elapsed_s": round(time.monotonic() - started_monotonic, 4),
                        "voltage_set_V": round(float(voltage), 6),
                        "voltage_measured_V": round(float(measured_voltage), 6),
                        "current_mA": round(led_current_mA, 9),
                        "photodiode_uA": round(
                            -float(photodiode_current_A) * 1_000_000.0,
                            9,
                        ),
                        "spectrum_peak_nm": round(float(wavelengths[peak_index]), 3),
                        "spectrum_peak_counts": round(float(intensities[peak_index]), 3),
                    }
                    self._record_point(point)
                    if led_current_mA >= 8.0:
                        with self._condition:
                            self._state["status"] = "safety_limit"
                            self._state["stop_reason"] = "current_limit"
                        self._stop_event.set()
                        self._log("PoC остановлен по защитному пределу 8 мА.")
                        break

                shutdown_confirmed = safe_shutdown_smu(smu)
                with self._condition:
                    if self._state["status"] == "safety_limit":
                        pass
                    elif self._stop_event.is_set():
                        self._state["status"] = "stopped"
                        self._state["stop_reason"] = self._state["stop_reason"] or "operator"
                    else:
                        self._state["status"] = "completed"
                    self._state["safe_shutdown_confirmed"] = shutdown_confirmed
                    self._state["finished_at"] = utc_now()
                    self._publish_state_locked()
                self._log(
                    "PoC завершён; выходы SMU безопасно отключены."
                    if shutdown_confirmed
                    else "PoC завершён без подтверждения отключения выходов SMU."
                )
        except Exception as exc:
            if smu is not None and not shutdown_confirmed:
                shutdown_confirmed = safe_shutdown_smu(smu)
            with self._condition:
                self._state["status"] = "failed"
                self._state["error"] = str(exc)
                self._state["safe_shutdown_confirmed"] = shutdown_confirmed
                self._state["finished_at"] = utc_now()
                self._publish_state_locked()
            self.logger.exception("Stage 2 simulator PoC failed")
            self._log(f"Ошибка PoC: {exc}")
        finally:
            with self._condition:
                self._active_smu = None
                self._thread = None
                if self._state["status"] in POC_ACTIVE_STATES:
                    self._state["status"] = "failed"
                    self._state["error"] = "PoC завершился без терминального состояния."
                    self._state["safe_shutdown_confirmed"] = shutdown_confirmed
                    self._state["finished_at"] = utc_now()
                self._publish_state_locked()
