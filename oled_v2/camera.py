"""Backend-owned free-camera session for the v2 desktop shell."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Callable, Optional

from oled_app.camera.client import (
    CameraClient,
    RemoteFile,
    build_camera_service_url,
    normalize_center_crop,
    safe_capture_stem,
)
from oled_app.utils import safe_filename, timestamp_for_file


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
        default_download_dir: Path | str = "camera_downloads",
        default_keep_remote: bool = True,
        default_crop: Optional[dict[str, Any]] = None,
        default_photo_settings: Optional[dict[str, str]] = None,
        default_video_settings: Optional[dict[str, str]] = None,
        series_service=None,
    ) -> None:
        try:
            initial_port = int(default_port)
        except (TypeError, ValueError):
            initial_port = 8765
        if not 1 <= initial_port <= 65535:
            initial_port = 8765
        self._client_factory = client_factory
        self._series_service = series_service
        self._logger = logger
        self._lock = threading.RLock()
        self._client: Optional[CameraClient] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_stop = threading.Event()
        self._latest_frame: Optional[bytes] = None
        self._recording_context: Optional[dict[str, Any]] = None
        try:
            initial_crop = normalize_center_crop(default_crop)
        except ValueError:
            initial_crop = normalize_center_crop()
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
            "recording_active": False,
            "recording_started_at": None,
            "mode": "free",
            "series_target": None,
            "frame_sequence": 0,
            "frame_size": 0,
            "frame_received_at": None,
            "preferences": {
                "crop": initial_crop,
                "photo_settings": dict(default_photo_settings or {}),
                "video_settings": dict(default_video_settings or {}),
                "keep_remote_files": bool(default_keep_remote),
                "download_dir": str(Path(default_download_dir).expanduser()),
            },
            "last_transfer": None,
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
            state["preferences"] = {
                **self._state["preferences"],
                "crop": dict(self._state["preferences"]["crop"]),
                "photo_settings": dict(self._state["preferences"]["photo_settings"]),
                "video_settings": dict(self._state["preferences"]["video_settings"]),
            }
            state["last_transfer"] = dict(self._state["last_transfer"] or {}) or None
            state["series_target"] = dict(self._state["series_target"] or {}) or None
            return state

    def select_series_target(self, target: dict[str, Any], station: str) -> dict[str, Any]:
        if self.snapshot()["recording_active"]:
            raise RuntimeError("Сначала завершите запись видео.")
        if self._series_service is None:
            raise RuntimeError("Режим камеры серии недоступен.")
        context = self._series_service.camera_target(target, station)
        public_target = {
            key: context[key]
            for key in ("series_path", "series_name", "pixel_id", "station", "station_label")
        }
        public_target["session_dir"] = None
        with self._lock:
            self._state.update({
                "mode": "series",
                "series_target": public_target,
                "message": (
                    f"Камера привязана к {context['station_label']} · {context['pixel_id']}."
                ),
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def clear_series_target(self) -> dict[str, Any]:
        if self.snapshot()["recording_active"]:
            raise RuntimeError("Сначала завершите запись видео.")
        with self._lock:
            self._state.update({
                "mode": "free",
                "series_target": None,
                "message": "Включён свободный режим камеры.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

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
            preferences = dict(self._state["preferences"])
            preferences["photo_settings"] = self._resolve_photo_settings(
                capabilities or {},
                preferences["photo_settings"],
            )
            preferences["video_settings"] = self._resolve_video_settings(
                capabilities or {},
                preferences["video_settings"],
            )
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
                "recording_active": bool((camera_status or {}).get("recording_active")),
                "recording_started_at": None,
                "preferences": preferences,
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
            capabilities = client.capabilities() if self.snapshot()["initialized"] else None
            files = [asdict(item) for item in client.list_files()]
        except Exception as exc:
            self._set_error("Не удалось обновить состояние камеры.", exc)
            raise
        with self._lock:
            preferences = dict(self._state["preferences"])
            preferences["photo_settings"] = self._resolve_photo_settings(
                capabilities or {},
                preferences["photo_settings"],
            )
            preferences["video_settings"] = self._resolve_video_settings(
                capabilities or {},
                preferences["video_settings"],
            )
            recording_active = bool((camera_status or {}).get("recording_active"))
            self._state.update({
                "health": health,
                "camera_status": camera_status,
                "capabilities": capabilities,
                "files": files,
                "preferences": preferences,
                "recording_active": recording_active,
                "recording_started_at": (
                    self._state["recording_started_at"] if recording_active else None
                ),
                "message": "Состояние камеры обновлено.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def update_preferences(
        self,
        photo_settings: Optional[dict[str, str]] = None,
        crop: Optional[dict[str, Any]] = None,
        keep_remote_files: Optional[bool] = None,
        video_settings: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        existing = self.snapshot()["preferences"]
        selected = self._validate_photo_settings(
            photo_settings if photo_settings is not None else existing["photo_settings"]
        )
        normalized_crop = normalize_center_crop(crop or existing["crop"])
        selected_video = self._validate_video_settings(
            video_settings if video_settings is not None else existing["video_settings"]
        )
        with self._lock:
            current = self._state["preferences"]
            self._state["preferences"] = {
                **current,
                "crop": normalized_crop,
                "photo_settings": selected,
                "video_settings": selected_video,
                "keep_remote_files": (
                    current["keep_remote_files"]
                    if keep_remote_files is None
                    else bool(keep_remote_files)
                ),
            }
            self._state.update({
                "message": "Параметры камеры сохранены.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def capture(
        self,
        kind: str,
        file_name: str = "",
        photo_settings: Optional[dict[str, str]] = None,
        crop: Optional[dict[str, Any]] = None,
        keep_remote_files: Optional[bool] = None,
    ) -> dict[str, Any]:
        if kind not in {"snapshot", "photo"}:
            raise ValueError("Поддерживаются только preview-кадр и полноразмерное фото.")
        client = self._require_client()
        current = self.snapshot()
        if current["recording_active"]:
            raise RuntimeError("Сначала остановите запись видео.")
        preferences = current["preferences"]
        selected_settings = self._validate_photo_settings(
            photo_settings if photo_settings is not None else preferences["photo_settings"]
        )
        selected_crop = normalize_center_crop(crop or preferences["crop"])
        keep_remote = (
            bool(preferences["keep_remote_files"])
            if keep_remote_files is None
            else bool(keep_remote_files)
        )
        if kind == "snapshot" and not current["liveview_active"]:
            raise RuntimeError("Сначала запустите LiveView для сохранения preview-кадра.")

        series_context, output_dir, requested_name = self._capture_destination(
            kind, str(file_name or "").strip()
        )

        restart_liveview = kind == "photo" and current["liveview_active"]
        if restart_liveview:
            self.stop_liveview()
        restart_error = ""
        try:
            remote = (
                client.save_liveview_snapshot(requested_name, selected_crop)
                if kind == "snapshot"
                else client.capture_photo(
                    selected_settings,
                    requested_name,
                    selected_crop,
                )
            )
            local = client.download_file(remote, output_dir)
            deleted = False
            delete_error = ""
            if not keep_remote:
                try:
                    client.delete_file(remote)
                    deleted = True
                except Exception as exc:
                    delete_error = str(exc)
            try:
                files = [asdict(item) for item in client.list_files()]
            except Exception:
                files = current["files"]
            try:
                camera_status = client.status()
            except Exception:
                camera_status = current["camera_status"]
            if series_context is not None:
                self._series_service.record_camera_file(
                    series_context,
                    kind,
                    local,
                    remote.name,
                    {"v2_camera_mode": "series"},
                )
        except Exception as exc:
            self._set_error("Не удалось создать или скачать файл камеры.", exc)
            raise
        finally:
            if restart_liveview:
                try:
                    self.start_liveview()
                except Exception as exc:
                    restart_error = str(exc)
                    self._set_error("Фото сохранено, но LiveView не удалось возобновить.", exc)

        transfer = {
            "action": kind,
            "remote": asdict(remote),
            "local_file": str(local),
            "remote_deleted": deleted,
            "delete_error": delete_error,
            "completed_at": _utc_now(),
            "series_target": self.snapshot().get("series_target"),
        }
        with self._lock:
            self._state.update({
                "files": files,
                "camera_status": camera_status,
                "last_transfer": transfer,
                "message": (
                    "Файл камеры скачан, но LiveView не удалось возобновить."
                    if restart_error
                    else "Файл камеры скачан и проверен."
                ),
                "error": delete_error or restart_error or None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def download(self, file_id: str) -> dict[str, Any]:
        client = self._require_client()
        remote = self._remote_file(file_id, client)
        preferences = self.snapshot()["preferences"]
        try:
            local = client.download_file(remote, preferences["download_dir"])
        except Exception as exc:
            self._set_error("Не удалось скачать файл камеры.", exc)
            raise
        transfer = {
            "action": "download",
            "remote": asdict(remote),
            "local_file": str(local),
            "remote_deleted": False,
            "delete_error": "",
            "completed_at": _utc_now(),
        }
        with self._lock:
            self._state.update({
                "last_transfer": transfer,
                "message": "Файл скачан и проверен.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def delete(self, file_id: str) -> dict[str, Any]:
        client = self._require_client()
        remote = self._remote_file(file_id, client)
        try:
            client.delete_file(remote)
            files = [asdict(item) for item in client.list_files()]
        except Exception as exc:
            self._set_error("Не удалось удалить удалённый файл камеры.", exc)
            raise
        with self._lock:
            self._state.update({
                "files": files,
                "message": f"Удалён файл {remote.name}.",
                "error": None,
                "updated_at": _utc_now(),
            })
        return self.snapshot()

    def start_recording(
        self,
        video_settings: Optional[dict[str, str]] = None,
        crop: Optional[dict[str, Any]] = None,
        keep_remote_files: Optional[bool] = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        current = self.snapshot()
        if current["recording_active"]:
            return current
        preferences = current["preferences"]
        selected_settings = self._validate_video_settings(
            video_settings if video_settings is not None else preferences["video_settings"]
        )
        selected_crop = normalize_center_crop(crop or preferences["crop"])
        keep_remote = (
            bool(preferences["keep_remote_files"])
            if keep_remote_files is None
            else bool(keep_remote_files)
        )
        series_context, output_dir, preferred_name = self._capture_destination("video", "")
        try:
            camera_status = client.start_recording(selected_settings, selected_crop)
        except Exception as exc:
            self._set_error("Не удалось запустить запись видео.", exc)
            raise

        with self._lock:
            updated_preferences = dict(self._state["preferences"])
            updated_preferences.update({
                "crop": selected_crop,
                "video_settings": selected_settings,
                "keep_remote_files": keep_remote,
            })
            self._state.update({
                "camera_status": camera_status,
                "recording_active": True,
                "recording_started_at": _utc_now(),
                "preferences": updated_preferences,
                "message": "Запись видео началась.",
                "error": None,
                "updated_at": _utc_now(),
            })
            self._recording_context = {
                "series": series_context,
                "output_dir": str(output_dir),
                "preferred_name": f"{preferred_name}.mp4" if preferred_name else "",
            }
        if not current["liveview_active"]:
            self._start_stream_reader(client, reset_frame=True)
        return self.snapshot()

    def stop_recording(self) -> dict[str, Any]:
        client = self._require_client()
        current = self.snapshot()
        if not current["recording_active"]:
            raise RuntimeError("Запись видео не запущена.")
        preferences = current["preferences"]
        recording_context = dict(self._recording_context or {})
        try:
            remote = client.stop_recording()
        except Exception as exc:
            self._set_error("Не удалось корректно завершить запись видео.", exc)
            raise

        with self._lock:
            self._state.update({
                "recording_active": False,
                "recording_started_at": None,
                "message": "Видео завершено; выполняется проверенное скачивание.",
                "error": None,
                "updated_at": _utc_now(),
            })
        try:
            local = client.download_file(
                remote,
                recording_context.get("output_dir") or preferences["download_dir"],
                preferred_name=str(recording_context.get("preferred_name") or ""),
            )
            deleted = False
            delete_error = ""
            if not preferences["keep_remote_files"]:
                try:
                    client.delete_file(remote)
                    deleted = True
                except Exception as exc:
                    delete_error = str(exc)
            files = [asdict(item) for item in client.list_files()]
            camera_status = client.status()
            series_context = recording_context.get("series")
            if series_context is not None:
                self._series_service.record_camera_file(
                    series_context,
                    "video",
                    local,
                    remote.name,
                    {"v2_camera_mode": "series"},
                )
        except Exception as exc:
            try:
                files = [asdict(item) for item in client.list_files()]
                camera_status = client.status()
            except Exception:
                files = current["files"]
                camera_status = current["camera_status"]
            with self._lock:
                self._state.update({
                    "files": files,
                    "camera_status": camera_status,
                    "message": "Видео завершено на Raspberry Pi, но скачать его не удалось.",
                    "error": str(exc),
                    "updated_at": _utc_now(),
                })
            raise

        transfer = {
            "action": "video",
            "remote": asdict(remote),
            "local_file": str(local),
            "remote_deleted": deleted,
            "delete_error": delete_error,
            "completed_at": _utc_now(),
            "series_target": self.snapshot().get("series_target"),
        }
        with self._lock:
            self._state.update({
                "files": files,
                "camera_status": camera_status,
                "last_transfer": transfer,
                "message": "Видео корректно завершено, скачано и проверено.",
                "error": delete_error or None,
                "updated_at": _utc_now(),
            })
            self._recording_context = None
        return self.snapshot()

    def start_liveview(self, video_settings: Optional[dict[str, str]] = None) -> dict[str, Any]:
        client = self._require_client()
        current = self.snapshot()
        with self._lock:
            if self._state["liveview_active"]:
                return self.snapshot()
        selected_settings = self._validate_video_settings(
            video_settings if video_settings is not None else current["preferences"]["video_settings"]
        )
        client.start_liveview(selected_settings)
        self._start_stream_reader(client, reset_frame=True)
        return self.snapshot()

    def _start_stream_reader(self, client: CameraClient, reset_frame: bool) -> None:
        with self._lock:
            if self._stream_thread is not None and self._stream_thread.is_alive():
                self._state["liveview_active"] = True
                return
            self._stream_stop = threading.Event()
            if reset_frame:
                self._latest_frame = None
            self._state.update({
                "liveview_active": True,
                "frame_sequence": 0 if reset_frame else self._state["frame_sequence"],
                "frame_size": 0 if reset_frame else self._state["frame_size"],
                "frame_received_at": None if reset_frame else self._state["frame_received_at"],
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

    def frame(self) -> bytes:
        with self._lock:
            if self._latest_frame is None:
                raise RuntimeError("Кадр LiveView ещё не получен.")
            return bytes(self._latest_frame)

    def stop_liveview(self) -> dict[str, Any]:
        if self.snapshot()["recording_active"]:
            raise RuntimeError("Сначала остановите запись видео.")
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
        if self.snapshot()["recording_active"]:
            self.stop_recording()
        try:
            self.stop_liveview()
        except Exception:
            if self._logger:
                self._logger.exception("Camera LiveView shutdown failed during disconnect")
        with self._lock:
            self._client = None
            self._latest_frame = None
            self._recording_context = None
            self._state.update({
                "connected": False,
                "initialized": False,
                "health": None,
                "camera_status": None,
                "capabilities": None,
                "files": [],
                "liveview_active": False,
                "recording_active": False,
                "recording_started_at": None,
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

    def _capture_destination(
        self,
        media_kind: str,
        suffix: str,
    ) -> tuple[Optional[dict[str, Any]], Path, str]:
        state = self.snapshot()
        target = state.get("series_target")
        if state.get("mode") != "series" or not target:
            return None, Path(state["preferences"]["download_dir"]), safe_capture_stem(suffix)
        if self._series_service is None:
            raise RuntimeError("Режим камеры серии недоступен.")
        request = {
            "series_path": target["series_path"],
            "pixel_id": target["pixel_id"],
        }
        if target.get("session_dir"):
            context = self._series_service.camera_target(request, target["station"])
            context["session_dir"] = target["session_dir"]
        else:
            context = self._series_service.create_camera_session(request, target["station"])
            with self._lock:
                if self._state.get("series_target"):
                    self._state["series_target"]["session_dir"] = context["session_dir"]
                    self._state["updated_at"] = _utc_now()
        parts = [
            safe_filename(context["pixel_id"], fallback="pixel"),
            context["station"],
            safe_filename(media_kind, fallback="capture"),
        ]
        safe_suffix = safe_capture_stem(suffix)
        if safe_suffix:
            parts.append(safe_suffix)
        parts.append(timestamp_for_file())
        return (
            context,
            Path(context["session_dir"]),
            safe_capture_stem("_".join(parts)),
        )

    def _validate_photo_settings(self, requested: dict[str, str]) -> dict[str, str]:
        with self._lock:
            capabilities = self._state.get("capabilities") or {}
        controls = {
            str(item.get("path")): item
            for group in ("photo_controls", "exposure_controls")
            for item in capabilities.get(group, [])
            if isinstance(item, dict) and item.get("path")
        }
        selected: dict[str, str] = {}
        for path, raw_value in requested.items():
            control = controls.get(str(path))
            value = str(raw_value)
            if control is None:
                raise ValueError(f"Камера не поддерживает параметр {path}.")
            choices = [str(item) for item in control.get("choices") or []]
            if value not in choices:
                raise ValueError(f"Недопустимое значение параметра {path}: {value}.")
            selected[str(path)] = value
        return selected

    def _validate_video_settings(self, requested: dict[str, str]) -> dict[str, str]:
        with self._lock:
            capabilities = self._state.get("capabilities") or {}
        controls = {
            str(item.get("path")): item
            for group in ("video_quality_controls", "video_fps_controls")
            for item in capabilities.get(group, [])
            if isinstance(item, dict) and item.get("path")
        }
        selected: dict[str, str] = {}
        for path, raw_value in requested.items():
            control = controls.get(str(path))
            value = str(raw_value)
            if control is None:
                raise ValueError(f"Камера не поддерживает видеопараметр {path}.")
            choices = [str(item) for item in control.get("choices") or []]
            if value not in choices:
                raise ValueError(f"Недопустимое значение видеопараметра {path}: {value}.")
            selected[str(path)] = value
        return selected

    @staticmethod
    def _resolve_photo_settings(
        capabilities: dict[str, Any],
        saved: dict[str, str],
    ) -> dict[str, str]:
        selected: dict[str, str] = {}
        for group in ("photo_controls", "exposure_controls"):
            for control in capabilities.get(group, []):
                if not isinstance(control, dict):
                    continue
                path = str(control.get("path") or "")
                choices = [str(item) for item in control.get("choices") or []]
                if not path or not choices:
                    continue
                value = str(saved.get(path) or control.get("current") or choices[0])
                selected[path] = value if value in choices else str(control.get("current") or choices[0])
        return selected

    @staticmethod
    def _resolve_video_settings(
        capabilities: dict[str, Any],
        saved: dict[str, str],
    ) -> dict[str, str]:
        selected: dict[str, str] = {}
        for group in ("video_quality_controls", "video_fps_controls"):
            for control in capabilities.get(group, []):
                if not isinstance(control, dict):
                    continue
                path = str(control.get("path") or "")
                choices = [str(item) for item in control.get("choices") or []]
                if not path or not choices:
                    continue
                value = str(saved.get(path) or control.get("current") or choices[0])
                selected[path] = value if value in choices else str(control.get("current") or choices[0])
        return selected

    def _remote_file(self, file_id: str, client: CameraClient) -> RemoteFile:
        selected_id = str(file_id or "").strip()
        if not selected_id:
            raise ValueError("Не указан идентификатор удалённого файла.")
        files = client.list_files()
        remote = next((item for item in files if item.file_id == selected_id), None)
        if remote is None:
            raise ValueError("Удалённый файл больше не найден на Raspberry Pi.")
        return remote

    def _set_error(self, message: str, exc: Exception) -> None:
        with self._lock:
            self._state.update({
                "message": message,
                "error": str(exc),
                "updated_at": _utc_now(),
            })
