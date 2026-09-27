"""Backend-owned free-camera session for the v2 desktop shell."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import threading
from typing import Any, Callable, Optional

from oled_app.camera.client import CameraClient, build_camera_service_url


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CameraController:
    """Own one Raspberry Pi camera client and its cancellable LiveView reader."""

    def __init__(
        self,
        client_factory: Callable[..., CameraClient] = CameraClient,
        logger=None,
        default_host: str = "192.168.4.1",
        default_port: int = 8765,
    ) -> None:
        try:
            initial_port = int(default_port)
        except (TypeError, ValueError):
            initial_port = 8765
        if not 1 <= initial_port <= 65535:
            initial_port = 8765
        self._client_factory = client_factory
        self._logger = logger
        self._lock = threading.RLock()
        self._client: Optional[CameraClient] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_stop = threading.Event()
        self._latest_frame: Optional[bytes] = None
        self._state: dict[str, Any] = {
            "connected": False,
            "base_url": None,
            "host": str(default_host or "192.168.4.1"),
            "port": initial_port,
            "initialized": False,
            "health": None,
            "camera_status": None,
            "capabilities": None,
            "files": [],
            "liveview_active": False,
            "frame_sequence": 0,
            "frame_size": 0,
            "frame_received_at": None,
            "message": "Камера не подключена.",
            "error": None,
            "updated_at": _utc_now(),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["files"] = [dict(item) for item in self._state["files"]]
            state["health"] = dict(self._state["health"] or {}) or None
            state["camera_status"] = dict(self._state["camera_status"] or {}) or None
            state["capabilities"] = dict(self._state["capabilities"] or {}) or None
            return state

    def connect(
        self,
        host: str,
        port: int | str,
        initialize: bool = True,
        request_timeout_s: float = 8.0,
        stream_timeout_s: float = 12.0,
    ) -> dict[str, Any]:
        try:
            selected_port = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Порт камеры должен быть целым числом.") from exc
        if not 1 <= selected_port <= 65535:
            raise ValueError("Порт камеры должен быть от 1 до 65535.")

        self.disconnect()
        base_url = build_camera_service_url(host, selected_port)
        client = self._client_factory(
            base_url,
            timeout_s=float(request_timeout_s),
            stream_timeout_s=float(stream_timeout_s),
        )
        try:
            health = client.health()
            initialized = False
            if initialize:
                client.initialize()
                initialized = True
            camera_status = client.status()
            capabilities = client.capabilities() if initialized else None
            files = [asdict(item) for item in client.list_files()]
        except Exception as exc:
            with self._lock:
                self._state.update({
                    "connected": False,
                    "base_url": base_url,
                    "host": str(host or "192.168.4.1").strip(),
                    "port": selected_port,
                    "initialized": False,
                    "message": "Не удалось подключиться к сервису камеры.",
                    "error": str(exc),
                    "updated_at": _utc_now(),
                })
            raise

        with self._lock:
            self._client = client
            self._state.update({
                "connected": True,
                "base_url": base_url,
                "host": str(host or "192.168.4.1").strip(),
                "port": selected_port,
                "initialized": initialized,
                "health": health,
                "camera_status": camera_status,
                "capabilities": capabilities,
                "files": files,
                "message": "Сервис камеры подключён.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def refresh(self) -> dict[str, Any]:
        client = self._require_client()
        try:
            health = client.health()
            camera_status = client.status()
            files = [asdict(item) for item in client.list_files()]
        except Exception as exc:
            self._set_error("Не удалось обновить состояние камеры.", exc)
            raise
        with self._lock:
            self._state.update({
                "health": health,
                "camera_status": camera_status,
                "files": files,
                "message": "Состояние камеры обновлено.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def start_liveview(self, video_settings: Optional[dict[str, str]] = None) -> dict[str, Any]:
        client = self._require_client()
        with self._lock:
            if self._state["liveview_active"]:
                return self.snapshot()
        client.start_liveview(video_settings or {})
        with self._lock:
            self._stream_stop = threading.Event()
            self._latest_frame = None
            self._state.update({
                "liveview_active": True,
                "frame_sequence": 0,
                "frame_size": 0,
                "frame_received_at": None,
                "message": "LiveView запущен; ожидаем первый кадр.",
                "error": None,
                "updated_at": _utc_now(),
            })
            thread = threading.Thread(
                target=self._read_liveview,
                args=(client, self._stream_stop),
                name="oled-v2-camera-liveview",
                daemon=True,
            )
            self._stream_thread = thread
            thread.start()
        return self.snapshot()

    def frame(self) -> bytes:
        with self._lock:
            if self._latest_frame is None:
                raise RuntimeError("Кадр LiveView ещё не получен.")
            return bytes(self._latest_frame)

    def stop_liveview(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            thread = self._stream_thread
            was_active = bool(self._state["liveview_active"])
            should_stop_service = was_active or thread is not None
            self._stream_stop.set()
        if client is not None:
            try:
                client.close_liveview_stream()
            except Exception:
                if self._logger:
                    self._logger.exception("Could not close the camera LiveView stream")
            if should_stop_service:
                try:
                    client.stop_liveview()
                except Exception as exc:
                    self._set_error("LiveView остановлен локально, но сервис не подтвердил остановку.", exc)
                    raise
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            self._stream_thread = None
            self._state.update({
                "liveview_active": False,
                "message": "LiveView остановлен." if was_active else self._state["message"],
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def disconnect(self) -> dict[str, Any]:
        try:
            self.stop_liveview()
        except Exception:
            if self._logger:
                self._logger.exception("Camera LiveView shutdown failed during disconnect")
        with self._lock:
            self._client = None
            self._latest_frame = None
            self._state.update({
                "connected": False,
                "initialized": False,
                "health": None,
                "camera_status": None,
                "capabilities": None,
                "files": [],
                "liveview_active": False,
                "frame_sequence": 0,
                "frame_size": 0,
                "frame_received_at": None,
                "message": "Камера отключена.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def shutdown(self) -> None:
        self.disconnect()

    def _read_liveview(self, client: CameraClient, stop_event: threading.Event) -> None:
        try:
            client.iter_liveview_frames(stop_event, self._accept_frame)
            if not stop_event.is_set():
                raise RuntimeError("Поток LiveView завершился неожиданно.")
        except Exception as exc:
            if not stop_event.is_set():
                self._set_error("Поток LiveView потерян.", exc)
        finally:
            with self._lock:
                if self._stream_thread is threading.current_thread():
                    self._state["liveview_active"] = False
                    self._state["updated_at"] = _utc_now()

    def _accept_frame(self, frame: bytes) -> None:
        if not frame:
            return
        with self._lock:
            self._latest_frame = bytes(frame)
            self._state["frame_sequence"] += 1
            self._state["frame_size"] = len(frame)
            self._state["frame_received_at"] = _utc_now()
            self._state["message"] = "LiveView активен."
            self._state["error"] = None
            self._state["updated_at"] = _utc_now()

    def _require_client(self) -> CameraClient:
        with self._lock:
            client = self._client
        if client is None:
            raise RuntimeError("Сначала подключитесь к сервису камеры.")
        return client

    def _set_error(self, message: str, exc: Exception) -> None:
        with self._lock:
            self._state.update({
                "message": message,
                "error": str(exc),
                "updated_at": _utc_now(),
            })
