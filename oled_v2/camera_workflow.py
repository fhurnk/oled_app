"""Backend-owned guided camera workflow for one series measurement."""

from __future__ import annotations

from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from oled_app.camera.telemetry_video import create_stability_telemetry_video

from .poc import utc_now


STABILITY_CURRENT_LIMIT_POSTROLL_S = 5.0


def stability_postroll_remaining_s(
    result: Optional[Dict[str, Any]],
    measurement_started_monotonic: Optional[float],
    now_monotonic: Optional[float] = None,
    postroll_s: float = STABILITY_CURRENT_LIMIT_POSTROLL_S,
) -> float:
    """Return post-roll still needed after a stability current-limit event."""

    event = next(
        (
            item
            for item in (result or {}).get("events", [])
            if str(item.get("event") or "") == "current_limit_or_breakdown"
        ),
        None,
    )
    if event is None:
        return 0.0
    if measurement_started_monotonic is None:
        return max(0.0, float(postroll_s))
    event_time = measurement_started_monotonic + float(event.get("measurement_time_s") or 0.0)
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    return max(0.0, float(postroll_s) - (now - event_time))


def write_video_measurement_timeline(
    video_path: Path,
    station: str,
    pixel_id: str,
    video_started_monotonic: float,
    video_stopped_monotonic: float,
    measurement_started_monotonic: float,
    measurement_status: str,
    measurement_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Write stable JSON/CSV clock mapping beside a guided measurement video."""

    duration = max(0.0, video_stopped_monotonic - video_started_monotonic)
    offset = video_started_monotonic - measurement_started_monotonic
    events = []
    for item in (measurement_result or {}).get("events", []):
        measurement_time = float(item.get("measurement_time_s") or 0.0)
        video_time = measurement_time - offset
        if 0.0 <= video_time <= duration:
            event = deepcopy(item)
            event["video_time_s"] = video_time
            events.append(event)
    sync_path = video_path.with_name(f"{video_path.stem}_sync.json")
    timeline_path = video_path.with_name(f"{video_path.stem}_timeline.csv")
    metadata = {
        "measurement_sync": True,
        "video_duration_clock_s": duration,
        "sync_file": sync_path.name,
        "timeline_file": timeline_path.name,
        "station": station,
        "measurement_type": "IVL" if station == "ivl" else "STABILITY",
        "measurement_status": measurement_status,
        "pixel_id": pixel_id,
        "measurement_time_at_video_start_s": offset,
        "mapping": "measurement_time_s = video_time_s + measurement_time_at_video_start_s",
        "events": events,
    }
    sync_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with timeline_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["video_time_s", "measurement_time_s", "measurement_type", "pixel_id", "event"])
        points = {float(second): "" for second in range(int(duration) + 1)}
        if not points or not math.isclose(max(points), duration, abs_tol=1e-6):
            points[duration] = ""
        for event in events:
            points[float(event["video_time_s"])] = str(
                event.get("label") or event.get("event") or ""
            )
        for video_time in sorted(points):
            writer.writerow([
                round(video_time, 3),
                round(video_time + offset, 3),
                metadata["measurement_type"],
                pixel_id,
                points[video_time],
            ])
    return {"sync_file": sync_path, "timeline_file": timeline_path, "metadata": metadata}


class GuidedCameraWorkflow:
    """Coordinate before-photo, video plus measurement, and after-photo."""

    def __init__(
        self,
        camera_controller,
        ivl_controller,
        stability_controller,
        telemetry_builder: Callable[[Path, Path], Path] = create_stability_telemetry_video,
        postroll_s: float = STABILITY_CURRENT_LIMIT_POSTROLL_S,
        logger=None,
    ) -> None:
        self.camera = camera_controller
        self.controllers = {
            "ivl": ivl_controller,
            "stability": stability_controller,
        }
        self.telemetry_builder = telemetry_builder
        self.postroll_s = max(0.0, float(postroll_s))
        self.logger = logger
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._cancel_requested = threading.Event()
        self._measurement_payload: Dict[str, Any] = {}
        self._measurement_started_monotonic: Optional[float] = None
        self._video_started_monotonic: Optional[float] = None
        self._state = self._idle_state()

    @staticmethod
    def _idle_state() -> Dict[str, Any]:
        return {
            "workflow_id": None,
            "status": "idle",
            "active": False,
            "station": None,
            "target": None,
            "measurement_run_id": None,
            "measurement_status": None,
            "measurement_result": None,
            "before_photo": None,
            "video_file": None,
            "sync_file": None,
            "timeline_file": None,
            "telemetry_file": None,
            "telemetry_error": None,
            "after_photo": None,
            "postroll_remaining_s": 0.0,
            "create_telemetry": False,
            "cancel_requested": False,
            "message": "Сопровождаемый сценарий не запущен.",
            "error": None,
            "started_at": None,
            "updated_at": utc_now(),
            "finished_at": None,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def prepare(
        self,
        station: str,
        measurement_payload: Dict[str, Any],
        create_telemetry: bool = True,
    ) -> Dict[str, Any]:
        station_key = str(station or "").strip().lower()
        if station_key not in self.controllers:
            raise ValueError("Станция сопровождаемой съёмки должна быть ivl или stability.")
        if not isinstance(measurement_payload, dict):
            raise ValueError("Параметры измерения должны быть объектом.")
        with self._lock:
            if self._state["active"]:
                raise RuntimeError("Сопровождаемый сценарий камеры уже выполняется.")

        camera_state = self.camera.snapshot()
        target = camera_state.get("series_target")
        if not camera_state.get("connected") or not camera_state.get("initialized"):
            raise RuntimeError("Сначала подключите и инициализируйте камеру.")
        if camera_state.get("recording_active"):
            raise RuntimeError("Перед сценарием завершите текущую запись видео.")
        if camera_state.get("mode") != "series" or not target:
            raise RuntimeError("Для сопровождаемой съёмки выберите пиксель серии.")
        if target.get("station") != station_key:
            raise RuntimeError("Станция камеры изменилась. Откройте нужное измерение заново.")
        requested_target = measurement_payload.get("target")
        expected_target = {
            "series_path": target["series_path"],
            "pixel_id": target["pixel_id"],
        }
        if requested_target != expected_target:
            raise RuntimeError("Пиксель измерения не совпадает с привязкой камеры.")
        if station_key == "ivl" and measurement_payload.get("queue"):
            raise ValueError("Сопровождаемая камера поддерживает один пиксель ВАЯХ, не очередь.")

        controller = self.controllers[station_key]
        controller.preflight(measurement_payload)
        workflow_id = uuid4().hex
        with self._lock:
            self._cancel_requested.clear()
            self._measurement_payload = deepcopy(measurement_payload)
            self._measurement_started_monotonic = None
            self._video_started_monotonic = None
            self._state = {
                **self._idle_state(),
                "workflow_id": workflow_id,
                "status": "capturing_before",
                "active": True,
                "station": station_key,
                "target": expected_target,
                "create_telemetry": bool(create_telemetry and station_key == "stability"),
                "message": "Создаётся контрольное фото до измерения.",
                "started_at": utc_now(),
                "updated_at": utc_now(),
            }
        try:
            camera = self.camera.capture("photo", "photo_before")
            before_photo = str((camera.get("last_transfer") or {}).get("local_file") or "")
            if not before_photo:
                raise RuntimeError("Камера не вернула путь начального фото.")
        except Exception as exc:
            self._fail("Не удалось сделать фото до измерения.", exc)
            raise
        with self._lock:
            self._state.update(
                status="awaiting_measurement",
                before_photo=before_photo,
                message=(
                    "Фото до измерения сохранено. Проверьте образец и подтвердите запуск "
                    "видео вместе с измерением."
                ),
                updated_at=utc_now(),
            )
        return self.snapshot()

    def continue_measurement(self) -> Dict[str, Any]:
        with self._lock:
            if not self._state["active"] or self._state["status"] != "awaiting_measurement":
                raise RuntimeError("Сценарий не ожидает запуска измерения.")
            station = self._state["station"]
            payload = deepcopy(self._measurement_payload)
            self._state.update(
                status="starting_measurement",
                message="Запускаются видеозапись и измерение.",
                updated_at=utc_now(),
            )
        controller = self.controllers[station]
        try:
            self.camera.start_recording()
            self._video_started_monotonic = time.monotonic()
            self._measurement_started_monotonic = time.monotonic()
            measurement = controller.start(payload)
        except Exception as exc:
            if self.camera.snapshot().get("recording_active"):
                try:
                    self.camera.stop_recording()
                except Exception:
                    if self.logger:
                        self.logger.exception("Could not finalize guided camera recording")
            self._fail("Не удалось запустить видео и измерение.", exc)
            raise
        with self._lock:
            self._state.update(
                status="measuring",
                measurement_run_id=measurement.get("run_id"),
                measurement_status=measurement.get("status"),
                message="Видео записывается вместе с измерением.",
                updated_at=utc_now(),
            )
            self._thread = threading.Thread(
                target=self._monitor_measurement,
                args=(station, controller),
                daemon=True,
                name="oled-v2-guided-camera",
            )
            self._thread.start()
        return self.snapshot()

    def finish(self, take_photo: bool = True) -> Dict[str, Any]:
        with self._lock:
            if not self._state["active"] or self._state["status"] != "awaiting_after_photo":
                raise RuntimeError("Сценарий ещё не готов к контрольному фото.")
            self._state.update(
                status="capturing_after" if take_photo else "completed",
                message=(
                    "Создаётся контрольное фото после измерения."
                    if take_photo else "Сценарий завершён без финального фото."
                ),
                updated_at=utc_now(),
            )
        if not take_photo:
            self._complete()
            return self.snapshot()
        try:
            camera = self.camera.capture("photo", "photo_after")
            after_photo = str((camera.get("last_transfer") or {}).get("local_file") or "")
            if not after_photo:
                raise RuntimeError("Камера не вернула путь финального фото.")
        except Exception as exc:
            with self._lock:
                self._state.update(
                    status="awaiting_after_photo",
                    message="Финальное фото не создано; можно повторить или завершить без него.",
                    error=str(exc),
                    updated_at=utc_now(),
                )
            raise
        with self._lock:
            self._state["after_photo"] = after_photo
        self._complete()
        return self.snapshot()

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            if not self._state["active"]:
                return self.snapshot()
            status = self._state["status"]
            self._cancel_requested.set()
            self._state.update(
                cancel_requested=True,
                message="Запрошено безопасное завершение сопровождаемого сценария.",
                updated_at=utc_now(),
            )
            station = self._state.get("station")
        if status in {"awaiting_measurement", "awaiting_after_photo"}:
            with self._lock:
                self._state.update(
                    status="cancelled",
                    active=False,
                    message="Сопровождаемый сценарий отменён.",
                    finished_at=utc_now(),
                    updated_at=utc_now(),
                )
            return self.snapshot()
        if station in self.controllers:
            self.controllers[station].stop()
        return self.snapshot()

    def shutdown(self) -> None:
        self.cancel()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=10.0)

    def _monitor_measurement(self, station: str, controller) -> None:
        measurement = controller.snapshot()
        while measurement.get("active"):
            self._update_measurement(measurement)
            time.sleep(0.05)
            measurement = controller.snapshot()
        self._update_measurement(measurement)
        result = deepcopy(measurement.get("result"))
        remaining = (
            stability_postroll_remaining_s(
                result,
                self._measurement_started_monotonic,
                postroll_s=self.postroll_s,
            )
            if station == "stability" else 0.0
        )
        if remaining > 0 and not self._cancel_requested.is_set():
            deadline = time.monotonic() + remaining
            while time.monotonic() < deadline and not self._cancel_requested.wait(0.05):
                left = max(0.0, deadline - time.monotonic())
                with self._lock:
                    self._state.update(
                        status="postroll",
                        postroll_remaining_s=left,
                        message=f"Лимит тока: камера записывает post-roll ещё {left:.1f} с.",
                        updated_at=utc_now(),
                    )

        with self._lock:
            self._state.update(
                status="processing_video",
                postroll_remaining_s=0.0,
                message="Видео завершается, скачивается и проверяется.",
                updated_at=utc_now(),
            )
        try:
            video_stopped_monotonic = time.monotonic()
            camera = self.camera.stop_recording()
            video_file = str((camera.get("last_transfer") or {}).get("local_file") or "")
            if not video_file:
                raise RuntimeError("Камера не вернула путь видео.")
        except Exception as exc:
            self._fail("Измерение завершено, но видео не удалось корректно сохранить.", exc)
            return

        sync_file = None
        timeline_file = None
        try:
            target = self.snapshot().get("target") or {}
            timeline = write_video_measurement_timeline(
                Path(video_file),
                station,
                str(target.get("pixel_id") or ""),
                self._video_started_monotonic or self._measurement_started_monotonic or video_stopped_monotonic,
                video_stopped_monotonic,
                self._measurement_started_monotonic or video_stopped_monotonic,
                str(measurement.get("status") or ""),
                result,
            )
            sync_file = str(timeline["sync_file"])
            timeline_file = str(timeline["timeline_file"])
            self.camera.record_series_derivative(
                timeline["sync_file"], "video_sync", Path(video_file).name,
                {"sidecar": True},
            )
            self.camera.record_series_derivative(
                timeline["timeline_file"], "video_timeline", Path(video_file).name,
                {"sidecar": True},
            )
        except Exception as exc:
            if self.logger:
                self.logger.exception("Could not write guided camera measurement timeline")

        telemetry_file = None
        telemetry_error = None
        if (station == "stability" and self.snapshot().get("create_telemetry")
                and result and result.get("file")):
            with self._lock:
                self._state.update(
                    status="processing_telemetry",
                    video_file=video_file,
                    message="Создаётся копия видео с показаниями стабильности.",
                    updated_at=utc_now(),
                )
            try:
                telemetry_path = self.telemetry_builder(Path(video_file), Path(result["file"]))
                self.camera.record_series_derivative(
                    telemetry_path,
                    "video_telemetry",
                    Path(video_file).name,
                    {"measurement_file": str(result["file"])},
                )
                telemetry_file = str(telemetry_path)
            except Exception as exc:
                telemetry_error = str(exc)
                if self.logger:
                    self.logger.exception("Could not build guided stability telemetry video")

        cancelled = self._cancel_requested.is_set()
        with self._lock:
            self._state.update(
                status="cancelled" if cancelled else "awaiting_after_photo",
                active=not cancelled,
                video_file=video_file,
                sync_file=sync_file,
                timeline_file=timeline_file,
                telemetry_file=telemetry_file,
                telemetry_error=telemetry_error,
                message=(
                    "Измерение безопасно остановлено; видео сохранено. Сценарий отменён."
                    if cancelled else
                    "Измерение и видео завершены. Сделайте контрольное фото после измерения."
                ),
                finished_at=utc_now() if cancelled else None,
                updated_at=utc_now(),
            )

    def _update_measurement(self, measurement: Dict[str, Any]) -> None:
        with self._lock:
            self._state.update(
                measurement_status=measurement.get("status"),
                measurement_result=deepcopy(measurement.get("result")),
                updated_at=utc_now(),
            )

    def _complete(self) -> None:
        with self._lock:
            self._state.update(
                status="completed",
                active=False,
                message="Сценарий «фото до → видео + измерение → фото после» завершён.",
                error=None,
                finished_at=utc_now(),
                updated_at=utc_now(),
            )

    def _fail(self, message: str, exc: Exception) -> None:
        with self._lock:
            self._state.update(
                status="failed",
                active=False,
                message=message,
                error=str(exc),
                finished_at=utc_now(),
                updated_at=utc_now(),
            )
