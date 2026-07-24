#!/usr/bin/env python3
"""Alpha HTTP service for a Canon camera connected to Raspberry Pi.

The service owns the only gphoto2 LiveView process.  The same MJPEG frames are
served to the desktop application and, while recording, copied to one ffmpeg
process.  This avoids restarting the camera when recording begins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool


LOGGER = logging.getLogger("oled_camera_service")
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
PHOTO_CONFIG_LABELS = {
    "imageformat": "Формат и качество фото",
    "imagequality": "Качество JPEG",
    "imagesize": "Размер фото",
    "resolution": "Разрешение фото",
    "quality": "Качество фото",
}
PHOTO_CONFIG_PRIORITY = {name: index for index, name in enumerate(PHOTO_CONFIG_LABELS)}
EXPOSURE_CONFIG_LABELS = {
    "iso": "ISO",
    "shutterspeed": "Выдержка",
    "shutterspeed2": "Выдержка",
    "aperture": "Диафрагма",
    "aperture2": "Диафрагма",
    "exposurecompensation": "Экспокоррекция",
    "exposurebiascompensation": "Экспокоррекция",
}
EXPOSURE_CONFIG_PRIORITY = {name: index for index, name in enumerate(EXPOSURE_CONFIG_LABELS)}
VIDEO_CONFIG_LABELS = {
    "liveviewsize": ("quality", "Качество/размер LiveView"),
    "output": ("quality", "Качество/размер LiveView Canon"),
    "moviequality": ("quality", "Режим качества камеры"),
    "videoquality": ("quality", "Качество видео камеры"),
    "moviesize": ("quality", "Размер видео камеры"),
    "videosize": ("quality", "Размер видео камеры"),
    "liveviewfps": ("fps", "Кадров в секунду LiveView"),
    "moviefps": ("fps", "Кадров в секунду камеры"),
    "movieframerate": ("fps", "Кадров в секунду камеры"),
    "videoframerate": ("fps", "Кадров в секунду камеры"),
    "framerate": ("fps", "Кадров в секунду камеры"),
}
VIDEO_CONFIG_PRIORITY = {name: index for index, name in enumerate(VIDEO_CONFIG_LABELS)}


def safe_capture_stem(name: str) -> str:
    """Normalize a requested JPEG name for Linux storage and Windows download."""

    value = Path(str(name or "").strip()).name
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value).strip(" .")
    value = re.sub(r"\.(?:jpe?g)$", "", value, flags=re.IGNORECASE).strip(" ._")
    return value[:100].rstrip(" ._")


def normalize_center_crop(crop: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Validate a centered crop expressed as retained width/height percentages."""

    value = crop or {}
    if not isinstance(value, dict):
        raise CameraServiceError("Некорректные параметры кадрирования.", "INVALID_CROP", status_code=400)
    try:
        width = float(str(value.get("width_percent", 100.0)).replace(",", "."))
        height = float(str(value.get("height_percent", 100.0)).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise CameraServiceError(
            "Ширина и высота кадрирования должны быть числами.",
            "INVALID_CROP",
            status_code=400,
        ) from exc
    if not 1.0 <= width <= 100.0 or not 1.0 <= height <= 100.0:
        raise CameraServiceError(
            "Ширина и высота кадрирования должны быть от 1 до 100%.",
            "INVALID_CROP",
            status_code=400,
        )
    return {"width_percent": round(width, 2), "height_percent": round(height, 2)}


def center_crop_filter(crop: Optional[Dict[str, Any]] = None) -> str:
    """Build an even-sized FFmpeg crop filter centered on the optical axis."""

    normalized = normalize_center_crop(crop)
    width = normalized["width_percent"]
    height = normalized["height_percent"]
    if width == 100.0 and height == 100.0:
        return ""
    width_ratio = width / 100.0
    height_ratio = height / 100.0
    return (
        f"crop=trunc(iw*{width_ratio:.6f}/2)*2:"
        f"trunc(ih*{height_ratio:.6f}/2)*2:(iw-ow)/2:(ih-oh)/2"
    )


def parse_gphoto_config(path: str, output: str) -> Dict[str, Any]:
    """Parse gphoto2 --get-config output without depending on its locale."""

    result: Dict[str, Any] = {
        "path": path,
        "label": "",
        "type": "",
        "current": "",
        "choices": [],
        "choice_indices": {},
        "readonly": False,
    }
    choices: list[str] = []
    choice_indices: Dict[str, int] = {}
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        choice_match = re.match(r"Choice\s*:\s*(\d+)\s+(.*)$", line, flags=re.IGNORECASE)
        if choice_match:
            choice_value = choice_match.group(2).strip()
            choices.append(choice_value)
            choice_indices[choice_value] = int(choice_match.group(1))
            continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        normalized = re.sub(r"[^a-zа-яё]", "", key.lower())
        if normalized in {"label", "метка"}:
            result["label"] = value
        elif normalized in {"type", "тип"}:
            result["type"] = value
        elif normalized in {"current", "текущий", "текущеезначение"}:
            result["current"] = value
        elif normalized in {"readonly", "толькочтение"}:
            result["readonly"] = value.lower() not in {"0", "false", "off", "no", "нет"}
    result["choices"] = choices
    result["choice_indices"] = choice_indices
    return result


def jpeg_dimensions(frame: bytes) -> Optional[tuple[int, int]]:
    """Read JPEG dimensions from a SOF marker without Pillow on Raspberry Pi."""

    if not frame.startswith(JPEG_START):
        return None
    offset = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(frame):
        if frame[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(frame) and frame[offset] == 0xFF:
            offset += 1
        if offset >= len(frame):
            return None
        marker = frame[offset]
        offset += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(frame):
            return None
        segment_length = int.from_bytes(frame[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(frame):
            return None
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(frame[offset + 3 : offset + 5], "big")
            width = int.from_bytes(frame[offset + 5 : offset + 7], "big")
            return (width, height) if width > 0 and height > 0 else None
        offset += segment_length
    return None


class CameraState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    LIVEVIEW = "LIVEVIEW"
    CAPTURING_PHOTO = "CAPTURING_PHOTO"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class CameraServiceError(RuntimeError):
    def __init__(self, message: str, error_code: str = "INTERNAL_ERROR", details: str = "", status_code: int = 409):
        super().__init__(message)
        self.error_code = error_code
        self.details = details
        self.status_code = status_code


@dataclass
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: str = "./camera_data"
    gphoto2_bin: str = "gphoto2"
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    command_timeout_s: float = 15.0
    photo_timeout_s: float = 45.0
    first_frame_timeout_s: float = 12.0
    no_frame_timeout_s: float = 10.0
    process_stop_timeout_s: float = 8.0
    min_free_disk_mb: int = 512
    ffmpeg_preset: str = "veryfast"
    ffmpeg_crf: int = 20
    ffmpeg_codec: str = "libx264"
    auto_initialize: bool = False
    allow_broad_gvfs_cleanup: bool = True

    @classmethod
    def load(cls, path: Optional[Path]) -> "ServiceConfig":
        config = cls()
        if path:
            values = json.loads(path.expanduser().read_text(encoding="utf-8"))
            for key, value in values.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        return config


@dataclass
class MediaRecord:
    file_id: str
    name: str
    kind: str
    path: str
    size: int
    created_at: str
    sha256: str = ""
    duration_s: Optional[float] = None

    def public(self) -> Dict[str, Any]:
        value = asdict(self)
        value.pop("path", None)
        return value


class CameraController:
    """Own gphoto2/ffmpeg processes and expose thread-safe camera operations."""

    def __init__(self, config: ServiceConfig):
        self.config = config
        self.data_dir = Path(config.data_dir).expanduser().resolve()
        self.photos_dir = self.data_dir / "photos"
        self.videos_dir = self.data_dir / "videos"
        self.temporary_dir = self.data_dir / "temporary"
        self.failed_dir = self.data_dir / "failed"
        self.logs_dir = self.data_dir / "logs"
        for folder in (self.photos_dir, self.videos_dir, self.temporary_dir, self.failed_dir, self.logs_dir):
            folder.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._frame_condition = threading.Condition(self._lock)
        self._first_frame = threading.Event()
        self._stream_stop_requested = threading.Event()
        self._record_stop_requested = threading.Event()
        self._record_queue: queue.Queue[bytes] = queue.Queue(maxsize=30)

        self.state = CameraState.DISCONNECTED
        self.camera_connected = False
        self.camera_model = ""
        self.last_error: Optional[str] = None
        self._gphoto: Optional[subprocess.Popen] = None
        self._gphoto_stderr: list[str] = []
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[bytes] = None
        self._frame_sequence = 0
        self._last_frame_at: Optional[datetime] = None
        self._frame_times: deque[float] = deque(maxlen=120)
        self._latest_frame_dimensions: Optional[tuple[int, int]] = None
        self._liveview_clients = 0
        self._liveview_idle_timer: Optional[threading.Timer] = None
        self._photo_controls: Dict[str, Dict[str, Any]] = {}
        self._exposure_controls: Dict[str, Dict[str, Any]] = {}
        self._video_controls: Dict[str, Dict[str, Any]] = {}
        self._viewfinder_control_path = ""
        self._viewfinder_off_verified: Optional[bool] = None

        self._ffmpeg: Optional[subprocess.Popen] = None
        self._ffmpeg_stderr: list[str] = []
        self._record_writer_thread: Optional[threading.Thread] = None
        self._recording_temp: Optional[Path] = None
        self._recording_final: Optional[Path] = None
        self._recording_started_at: Optional[datetime] = None
        self._recorded_frames = 0
        self._dropped_frames = 0
        self._recording_error: Optional[str] = None
        self._recording_crop: Dict[str, float] = normalize_center_crop()

        self._files: Dict[str, MediaRecord] = {}
        self._index_existing_files()

    def health(self) -> Dict[str, Any]:
        return {"success": True, "service": "camera", "status": "ok", "version": "1.8.4"}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            stream_active = self._process_running(self._gphoto)
            recording_active = self._process_running(self._ffmpeg)
            if self._ffmpeg is not None and not recording_active and self.state == CameraState.RECORDING:
                details = "\n".join(self._ffmpeg_stderr[-20:])
                self.last_error = details or "Процесс ffmpeg неожиданно завершился."
                self.state = CameraState.ERROR
            if stream_active and self._last_frame_at is not None:
                frame_age_s = (datetime.now(timezone.utc) - self._last_frame_at).total_seconds()
                if frame_age_s > float(self.config.no_frame_timeout_s):
                    self.last_error = f"LiveView не выдаёт новые кадры {frame_age_s:.1f} с."
                    if not recording_active:
                        self.state = CameraState.ERROR
            fps = self._current_fps_locked()
            free_mb = int(shutil.disk_usage(self.data_dir).free / (1024 * 1024))
            frame_width, frame_height = self._latest_frame_dimensions or (None, None)
            return {
                "success": True,
                "camera_connected": self.camera_connected,
                "camera_model": self.camera_model or None,
                "state": self.state.value,
                "liveview_active": stream_active,
                "liveview_clients": self._liveview_clients,
                "viewfinder_control": self._viewfinder_control_path or None,
                "viewfinder_off_verified": self._viewfinder_off_verified,
                "recording_active": recording_active,
                "recording_crop": dict(self._recording_crop),
                "last_frame_at": self._iso(self._last_frame_at),
                "fps": round(fps, 2),
                "frame_width": frame_width,
                "frame_height": frame_height,
                "current_file": self._recording_final.name if self._recording_final else None,
                "recorded_frames": self._recorded_frames,
                "dropped_frames": self._dropped_frames,
                "free_disk_mb": free_mb,
                "last_error": self.last_error,
            }

    def initialize(self) -> Dict[str, Any]:
        with self._operation("Камера уже выполняет другую операцию."):
            if self._process_running(self._ffmpeg):
                raise CameraServiceError("Нельзя переинициализировать камеру во время записи.", "CAMERA_BUSY")
            if self._ffmpeg is not None:
                self._abort_recording_to_failed()
            self.stop_stream(allow_recording=False)
            with self._lock:
                self.state = CameraState.INITIALIZING
                self.last_error = None
            self._check_binary(self.config.gphoto2_bin, "gphoto2")
            self._cleanup_gvfs(broad=False)
            detect = self._run_command([self.config.gphoto2_bin, "--auto-detect"], self.config.command_timeout_s)
            summary = self._run_command([self.config.gphoto2_bin, "--summary"], self.config.command_timeout_s, check=False)
            if summary.returncode != 0 and self.config.allow_broad_gvfs_cleanup and self._camera_busy(summary.stderr):
                self._cleanup_gvfs(broad=True)
                summary = self._run_command([self.config.gphoto2_bin, "--summary"], self.config.command_timeout_s, check=False)
            if summary.returncode != 0:
                details = (summary.stderr or summary.stdout).strip()
                with self._lock:
                    self.camera_connected = False
                    self.state = CameraState.DISCONNECTED if self._camera_missing(details) else CameraState.ERROR
                    self.last_error = details or "gphoto2 --summary завершился с ошибкой."
                code = "CAMERA_NOT_FOUND" if self._camera_missing(details) else "CAMERA_INITIALIZATION_FAILED"
                raise CameraServiceError("Камера Canon не готова к работе.", code, details)
            model = self._parse_camera_model(detect.stdout, summary.stdout)
            try:
                photo_controls = self._discover_photo_controls()
            except Exception:
                LOGGER.exception("Could not discover camera photo quality controls")
                photo_controls = []
            try:
                exposure_controls = self._discover_exposure_controls()
            except Exception:
                LOGGER.exception("Could not discover camera exposure controls")
                exposure_controls = []
            try:
                video_controls = self._discover_video_controls()
            except Exception:
                LOGGER.exception("Could not discover camera video controls")
                video_controls = []
            with self._lock:
                self.camera_connected = True
                self.camera_model = model or "Canon / gPhoto2 camera"
                self._photo_controls = {str(item["path"]): item for item in photo_controls}
                self._exposure_controls = {str(item["path"]): item for item in exposure_controls}
                self._video_controls = {str(item["path"]): item for item in video_controls}
                self._latest_frame_dimensions = None
                self._viewfinder_off_verified = None
                self.state = CameraState.READY
                self.last_error = None
            LOGGER.info("Camera initialized: %s", self.camera_model)
            return self.status()

    def capabilities(self) -> Dict[str, Any]:
        with self._lock:
            if not self.camera_connected:
                raise CameraServiceError("Сначала инициализируйте камеру.", "CAMERA_NOT_FOUND")
            controls = [dict(value) for value in self._photo_controls.values()]
            exposure_controls = [dict(value) for value in self._exposure_controls.values()]
            video_controls = [dict(value) for value in self._video_controls.values()]
            dimensions = list(self._latest_frame_dimensions) if self._latest_frame_dimensions else None
        return {
            "success": True,
            "camera_model": self.camera_model,
            "photo_controls": controls,
            "photo_note": "Показываются только безопасные JPEG-варианты, обнаруженные у подключённой камеры.",
            "exposure_controls": exposure_controls,
            "exposure_note": (
                "Показываются только доступные для изменения ISO, выдержка, диафрагма и экспокоррекция. "
                "После смены режима на диске камеры выполните повторную инициализацию."
            ),
            "video_quality_controls": [item for item in video_controls if item.get("role") == "quality"],
            "video_fps_controls": [item for item in video_controls if item.get("role") == "fps"],
            "liveview_resolution": dimensions,
            "video_note": "Показываются только параметры LiveView/video, которые сообщила камера. Если параметра FPS нет, частоту задаёт сама камера.",
        }

    def _discover_photo_controls(self) -> list[Dict[str, Any]]:
        return self._discover_choice_controls(PHOTO_CONFIG_LABELS, PHOTO_CONFIG_PRIORITY, jpeg_only=True)

    def _discover_exposure_controls(self) -> list[Dict[str, Any]]:
        return self._discover_choice_controls(EXPOSURE_CONFIG_LABELS, EXPOSURE_CONFIG_PRIORITY)

    def _discover_choice_controls(
        self,
        labels: Dict[str, str],
        priorities: Dict[str, int],
        jpeg_only: bool = False,
    ) -> list[Dict[str, Any]]:
        result = self._run_command(
            [self.config.gphoto2_bin, "--list-config"],
            self.config.command_timeout_s,
            check=False,
        )
        if result.returncode != 0:
            LOGGER.warning("gphoto2 --list-config failed: %s", (result.stderr or result.stdout).strip())
            return []
        candidates: list[tuple[int, str, str]] = []
        seen: set[str] = set()
        for raw_path in result.stdout.splitlines():
            path = raw_path.strip()
            if not path.startswith("/") or path in seen:
                continue
            basename = re.sub(r"[^a-z0-9]", "", path.rsplit("/", 1)[-1].lower())
            if basename not in labels:
                continue
            candidates.append((priorities[basename], path, basename))
            seen.add(path)

        controls: list[Dict[str, Any]] = []
        for _priority, path, basename in sorted(candidates):
            details = self._run_command(
                [self.config.gphoto2_bin, "--get-config", path],
                self.config.command_timeout_s,
                check=False,
            )
            if details.returncode != 0:
                continue
            control = parse_gphoto_config(path, details.stdout)
            choices = [str(value) for value in control.get("choices") or []]
            if jpeg_only and basename == "imageformat":
                choices = [value for value in choices if "jpeg" in value.lower() and "raw" not in value.lower()]
            if control.get("readonly") or len(choices) < 2:
                continue
            current = str(control.get("current") or "")
            if current not in choices:
                current = choices[0]
            controls.append(
                {
                    "path": path,
                    "key": basename,
                    "label": labels[basename],
                    "current": current,
                    "choices": choices,
                    "choice_indices": {
                        value: int(control.get("choice_indices", {}).get(value))
                        for value in choices
                        if control.get("choice_indices", {}).get(value) is not None
                    },
                }
            )
        return controls

    def _apply_photo_settings(self, requested: Optional[Dict[str, str]]) -> None:
        if not requested:
            return
        with self._lock:
            controls = {
                path: dict(value)
                for path, value in {**self._photo_controls, **self._exposure_controls}.items()
            }
        for path, requested_value in requested.items():
            control = controls.get(str(path))
            value = str(requested_value)
            if not control:
                raise CameraServiceError("Параметр фото не поддерживается этой камерой.", "UNSUPPORTED_CAMERA_SETTING", str(path), 400)
            if value not in control.get("choices", []):
                raise CameraServiceError("Недопустимое значение параметра фото.", "UNSUPPORTED_CAMERA_SETTING", value, 400)
            label = str(control.get("label") or path)
            attempts = [("--set-config-value", value)]
            choice_index = control.get("choice_indices", {}).get(value)
            if choice_index is not None:
                attempts.append(("--set-config-index", str(choice_index)))

            actual = ""
            failure_details = ""
            for option, option_value in attempts:
                applied = self._run_command(
                    [self.config.gphoto2_bin, option, f"{path}={option_value}"],
                    self.config.command_timeout_s,
                    check=False,
                )
                if applied.returncode != 0:
                    failure_details = (applied.stderr or applied.stdout).strip()
                    continue
                verify = self._run_command(
                    [self.config.gphoto2_bin, "--get-config", str(path)],
                    self.config.command_timeout_s,
                    check=False,
                )
                if verify.returncode != 0:
                    failure_details = (verify.stderr or verify.stdout).strip()
                    continue
                verified = parse_gphoto_config(str(path), verify.stdout)
                actual = str(verified.get("current") or "")
                if actual == value:
                    break
            else:
                details = f"{label} ({path}): запрошено {value}; установлено {actual or 'не определено'}"
                if failure_details:
                    details += f"; gPhoto2: {failure_details}"
                raise CameraServiceError(
                    f"Камера не сохранила параметр «{label}»: запрошено {value}, установлено {actual or 'не определено'}.",
                    "CAMERA_SETTING_FAILED",
                    details,
                )
            with self._lock:
                if str(path) in self._photo_controls:
                    self._photo_controls[str(path)]["current"] = actual
                if str(path) in self._exposure_controls:
                    self._exposure_controls[str(path)]["current"] = actual

    def _discover_video_controls(self) -> list[Dict[str, Any]]:
        result = self._run_command(
            [self.config.gphoto2_bin, "--list-config"],
            self.config.command_timeout_s,
            check=False,
        )
        if result.returncode != 0:
            return []
        candidates: list[tuple[int, str, str]] = []
        viewfinder_path = ""
        for raw_path in result.stdout.splitlines():
            path = raw_path.strip()
            if not path.startswith("/"):
                continue
            basename = re.sub(r"[^a-z0-9]", "", path.rsplit("/", 1)[-1].lower())
            if basename in {"viewfinder", "eosviewfinder"} and not viewfinder_path:
                viewfinder_path = path
            if basename in VIDEO_CONFIG_LABELS:
                candidates.append((VIDEO_CONFIG_PRIORITY[basename], path, basename))
        with self._lock:
            self._viewfinder_control_path = viewfinder_path
        controls: list[Dict[str, Any]] = []
        for _priority, path, basename in sorted(candidates):
            details = self._run_command(
                [self.config.gphoto2_bin, "--get-config", path],
                self.config.command_timeout_s,
                check=False,
            )
            if details.returncode != 0:
                continue
            control = parse_gphoto_config(path, details.stdout)
            choices = [str(value) for value in control.get("choices") or []]
            if control.get("readonly") or len(choices) < 2:
                continue
            role, label = VIDEO_CONFIG_LABELS[basename]
            current = str(control.get("current") or "")
            if current not in choices:
                current = choices[0]
            controls.append(
                {
                    "path": path,
                    "key": basename,
                    "role": role,
                    "label": label,
                    "current": current,
                    "choices": choices,
                }
            )
        return controls

    def _apply_video_settings(self, requested: Optional[Dict[str, str]]) -> None:
        if not requested:
            return
        with self._lock:
            controls = {path: dict(value) for path, value in self._video_controls.items()}
        for path, requested_value in requested.items():
            control = controls.get(str(path))
            value = str(requested_value)
            if not control or value not in control.get("choices", []):
                raise CameraServiceError(
                    "Камера не поддерживает выбранный режим видео.",
                    "UNSUPPORTED_CAMERA_VIDEO_SETTING",
                    f"{path}={value}",
                    400,
                )
            if value == str(control.get("current") or ""):
                continue
            applied = self._run_command(
                [self.config.gphoto2_bin, "--set-config-value", f"{path}={value}"],
                self.config.command_timeout_s,
                check=False,
            )
            if applied.returncode != 0:
                raise CameraServiceError(
                    "Камера не применила выбранный режим видео.",
                    "CAMERA_VIDEO_SETTING_FAILED",
                    (applied.stderr or applied.stdout).strip(),
                )
            with self._lock:
                if str(path) in self._video_controls:
                    self._video_controls[str(path)]["current"] = value

    def _video_settings_require_change(self, requested: Optional[Dict[str, str]]) -> bool:
        if not requested:
            return False
        with self._lock:
            controls = {path: dict(value) for path, value in self._video_controls.items()}
        changed = False
        for path, requested_value in requested.items():
            control = controls.get(str(path))
            value = str(requested_value)
            if not control or value not in control.get("choices", []):
                raise CameraServiceError(
                    "Камера не поддерживает выбранный режим видео.",
                    "UNSUPPORTED_CAMERA_VIDEO_SETTING",
                    f"{path}={value}",
                    400,
                )
            changed = changed or value != str(control.get("current") or "")
        return changed

    def start_stream(self, video_settings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        settings_changed = self._video_settings_require_change(video_settings)
        if settings_changed:
            with self._lock:
                if self._process_running(self._gphoto):
                    raise CameraServiceError("Остановите LiveView перед сменой режима камеры.", "CAMERA_BUSY")
            self._apply_video_settings(video_settings)
        with self._operation("Камера уже выполняет другую операцию."):
            with self._lock:
                if self._process_running(self._gphoto):
                    if self.state != CameraState.RECORDING:
                        self.state = CameraState.LIVEVIEW
                    return self.status()
                if not self.camera_connected:
                    raise CameraServiceError("Сначала подключите и инициализируйте камеру.", "CAMERA_NOT_FOUND")
                if self.state not in {CameraState.READY, CameraState.ERROR, CameraState.LIVEVIEW}:
                    raise CameraServiceError("Камера занята другой операцией.", "CAMERA_BUSY")
                self._first_frame.clear()
                self._stream_stop_requested.clear()
                self._latest_frame = None
                self._last_frame_at = None
                self._frame_times.clear()
                self.last_error = None
                self._viewfinder_off_verified = False if self._viewfinder_control_path else None
                command = [self.config.gphoto2_bin, "--stdout", "--capture-movie"]
                LOGGER.info("Starting LiveView: %s", command)
                try:
                    self._gphoto = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        bufsize=0,
                    )
                except OSError as exc:
                    self.state = CameraState.ERROR
                    self.last_error = str(exc)
                    raise CameraServiceError("Не удалось запустить gphoto2.", "LIVEVIEW_START_FAILED", str(exc)) from exc
                self._gphoto_stderr = []
                process = self._gphoto
                self._reader_thread = threading.Thread(target=self._stream_reader_loop, args=(process,), name="gphoto-reader", daemon=True)
                self._stderr_thread = threading.Thread(
                    target=self._collect_stderr,
                    args=(process, self._gphoto_stderr, "gphoto2"),
                    name="gphoto-stderr",
                    daemon=True,
                )
                self._reader_thread.start()
                self._stderr_thread.start()
            if not self._first_frame.wait(self.config.first_frame_timeout_s):
                self._stop_gphoto_process()
                details = "\n".join(self._gphoto_stderr[-20:])
                with self._lock:
                    self.state = CameraState.ERROR
                    self.last_error = details or "Первый кадр LiveView не получен вовремя."
                raise CameraServiceError("LiveView не выдал первый кадр.", "LIVEVIEW_START_FAILED", self.last_error or "")
            with self._lock:
                if self.state != CameraState.RECORDING:
                    self.state = CameraState.LIVEVIEW
            return self.status()

    def stop_stream(self, allow_recording: bool = False) -> Dict[str, Any]:
        process_was_running = False
        should_disable_viewfinder = False
        with self._frame_condition:
            idle_timer = self._liveview_idle_timer
            self._liveview_idle_timer = None
            if idle_timer is not None and idle_timer is not threading.current_thread():
                idle_timer.cancel()
            if self._process_running(self._ffmpeg) and not allow_recording:
                raise CameraServiceError("Сначала остановите запись видео.", "CAMERA_BUSY")
            process = self._gphoto
            if not self._process_running(process):
                self._gphoto = None
                if self.camera_connected and self.state not in {CameraState.CAPTURING_PHOTO, CameraState.STOPPING}:
                    self.state = CameraState.READY
                should_disable_viewfinder = self.camera_connected and bool(self._viewfinder_control_path)
            else:
                process_was_running = True
                should_disable_viewfinder = self.camera_connected and bool(self._viewfinder_control_path)
                self.state = CameraState.STOPPING
                self._stream_stop_requested.set()
                self._frame_condition.notify_all()
        if not process_was_running:
            if should_disable_viewfinder:
                self._disable_camera_viewfinder()
            return self.status()
        self._stop_process(process, "gphoto2")
        reader = self._reader_thread
        stderr_reader = self._stderr_thread
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        if stderr_reader and stderr_reader is not threading.current_thread():
            stderr_reader.join(timeout=2.0)
        for pipe in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        if should_disable_viewfinder:
            self._disable_camera_viewfinder()
        with self._lock:
            self._gphoto = None
            self._reader_thread = None
            self._stderr_thread = None
            self._latest_frame = None
            self._frame_condition.notify_all()
            self.state = CameraState.READY if self.camera_connected else CameraState.DISCONNECTED
        return self.status()

    def _disable_camera_viewfinder(self) -> Optional[bool]:
        with self._lock:
            path = self._viewfinder_control_path
        if not path:
            with self._lock:
                self._viewfinder_off_verified = None
            return None

        timeout_s = min(max(float(self.config.command_timeout_s), 1.0), 5.0)
        try:
            applied = self._run_command(
                [self.config.gphoto2_bin, "--set-config-value", f"{path}=0"],
                timeout_s,
                check=False,
            )
            verified = self._run_command(
                [self.config.gphoto2_bin, "--get-config", path],
                timeout_s,
                check=False,
            )
        except Exception as exc:
            with self._lock:
                self._viewfinder_off_verified = False
            LOGGER.warning("Could not switch off Canon viewfinder: %s", exc)
            return False
        current = ""
        if verified.returncode == 0:
            current = str(parse_gphoto_config(path, verified.stdout).get("current") or "").strip().lower()
        is_off = verified.returncode == 0 and current in {"0", "off", "false", "no", "none", "выкл", "выключено"}
        with self._lock:
            self._viewfinder_off_verified = is_off
        if not is_off:
            details = (verified.stderr or verified.stdout or applied.stderr or applied.stdout).strip()
            LOGGER.warning("Could not verify that Canon viewfinder is off: %s", details or path)
        return is_off

    def snapshot(self, file_name: str = "", crop: Optional[Dict[str, Any]] = None) -> MediaRecord:
        with self._operation("Камера уже выполняет другую операцию."):
            with self._lock:
                frame = self._latest_frame
            if not frame:
                raise CameraServiceError("Сначала запустите LiveView и дождитесь кадра.", "INVALID_STATE")
            path = self._photo_output_path(file_name, "camera_preview")
            try:
                path.write_bytes(frame)
                self._crop_image_in_place(path, crop)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return self._register_file(path, "preview")

    def capture_photo(
        self,
        photo_settings: Optional[Dict[str, str]] = None,
        file_name: str = "",
        crop: Optional[Dict[str, Any]] = None,
    ) -> MediaRecord:
        restart_stream = False
        record: Optional[MediaRecord] = None
        pending_error: Optional[Exception] = None
        with self._operation("Камера уже выполняет другую операцию."):
            with self._lock:
                if self._process_running(self._ffmpeg):
                    raise CameraServiceError("Полноразмерное фото недоступно во время записи.", "CAMERA_BUSY")
                restart_stream = self._process_running(self._gphoto)
            if restart_stream:
                self.stop_stream()
            with self._lock:
                if not self.camera_connected:
                    raise CameraServiceError("Камера не подключена.", "CAMERA_NOT_FOUND")
                self.state = CameraState.CAPTURING_PHOTO
            path = self._photo_output_path(file_name, "camera_photo")
            command = [
                self.config.gphoto2_bin,
                "--filename",
                str(path),
                "--capture-image-and-download",
            ]
            try:
                self._apply_photo_settings(photo_settings)
                result = self._run_command(command, self.config.photo_timeout_s, check=False)
                if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
                    details = (result.stderr or result.stdout).strip()
                    raise CameraServiceError("Не удалось сделать фото камерой.", "PHOTO_CAPTURE_FAILED", details)
                self._crop_image_in_place(path, crop)
                record = self._register_file(path, "photo")
                with self._lock:
                    self.state = CameraState.READY
                    self.last_error = None
            except Exception as exc:
                path.unlink(missing_ok=True)
                with self._lock:
                    self.state = CameraState.ERROR
                    self.last_error = str(exc)
                pending_error = exc
        if restart_stream and self.camera_connected:
            try:
                self.start_stream()
            except Exception as restart_exc:
                with self._lock:
                    self.last_error = f"LiveView не восстановлен после фото: {restart_exc}"
                if pending_error is None:
                    LOGGER.exception("Photo was saved but LiveView restart failed")
        if pending_error is not None:
            raise pending_error
        assert record is not None
        return record

    def start_recording(
        self,
        video_settings: Optional[Dict[str, str]] = None,
        crop: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected_crf = min(max(int(self.config.ffmpeg_crf), 0), 51)
        normalized_crop = normalize_center_crop(crop)
        crop_filter = center_crop_filter(normalized_crop)
        settings_changed = self._video_settings_require_change(video_settings)
        with self._lock:
            already_streaming = self._process_running(self._gphoto)
        if already_streaming and settings_changed:
            self.stop_stream()
            already_streaming = False
        if not already_streaming:
            self.start_stream(video_settings)
        with self._operation("Камера уже выполняет другую операцию."):
            with self._lock:
                if self._process_running(self._ffmpeg):
                    raise CameraServiceError("Запись видео уже идёт.", "VIDEO_ALREADY_RECORDING")
                stale_ffmpeg = self._ffmpeg is not None
            if stale_ffmpeg:
                self._abort_recording_to_failed()
            with self._lock:
                free_mb = int(shutil.disk_usage(self.data_dir).free / (1024 * 1024))
                if free_mb < int(self.config.min_free_disk_mb):
                    raise CameraServiceError(
                        f"На Raspberry Pi недостаточно места: {free_mb} МБ.",
                        "INSUFFICIENT_DISK_SPACE",
                    )
                self._check_binary(self.config.ffmpeg_bin, "ffmpeg")
                final_path = self.videos_dir / self._timestamped_name("camera_video", ".mp4")
                temp_path = self.temporary_dir / (final_path.name + ".part")
                command = [
                    self.config.ffmpeg_bin,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-fflags",
                    "+genpts",
                    "-use_wallclock_as_timestamps",
                    "1",
                    "-f",
                    "mjpeg",
                    "-i",
                    "pipe:0",
                    "-an",
                ]
                if crop_filter:
                    command.extend(["-vf", crop_filter])
                command.extend([
                    "-c:v",
                    str(self.config.ffmpeg_codec),
                    "-preset",
                    str(self.config.ffmpeg_preset),
                    "-crf",
                    str(selected_crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "vfr",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temp_path),
                ])
                LOGGER.info("Starting recording: %s", command)
                try:
                    self._ffmpeg = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                except OSError as exc:
                    raise CameraServiceError("Не удалось запустить ffmpeg.", "FFMPEG_START_FAILED", str(exc)) from exc
                self._ffmpeg_stderr = []
                self._recording_temp = temp_path
                self._recording_final = final_path
                self._recording_started_at = datetime.now(timezone.utc)
                self._recorded_frames = 0
                self._dropped_frames = 0
                self._recording_error = None
                self._recording_crop = normalized_crop
                self._clear_record_queue()
                self._record_stop_requested.clear()
                process = self._ffmpeg
                threading.Thread(
                    target=self._collect_stderr,
                    args=(process, self._ffmpeg_stderr, "ffmpeg"),
                    name="ffmpeg-stderr",
                    daemon=True,
                ).start()
                self._record_writer_thread = threading.Thread(target=self._record_writer_loop, args=(process,), name="ffmpeg-writer", daemon=True)
                self._record_writer_thread.start()

            deadline = time.monotonic() + min(max(self.config.first_frame_timeout_s, 2.0), 8.0)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    details = "\n".join(self._ffmpeg_stderr[-30:])
                    self._reset_failed_recording()
                    raise CameraServiceError("FFmpeg завершился при запуске.", "FFMPEG_START_FAILED", details)
                if temp_path.exists() and temp_path.stat().st_size > 0 and self._recorded_frames > 0:
                    with self._lock:
                        self.state = CameraState.RECORDING
                    return self.status()
                time.sleep(0.1)
            self._abort_recording_to_failed()
            raise CameraServiceError("FFmpeg не начал записывать кадры вовремя.", "FFMPEG_START_FAILED")

    def stop_recording(self) -> MediaRecord:
        with self._operation("Запись уже останавливается."):
            with self._lock:
                process = self._ffmpeg
                temp_path = self._recording_temp
                final_path = self._recording_final
                if not self._process_running(process):
                    raise CameraServiceError("Запись видео не запущена.", "VIDEO_NOT_RECORDING")
                self.state = CameraState.STOPPING
                self._record_stop_requested.set()
            writer = self._record_writer_thread
            if writer:
                writer.join(timeout=self.config.process_stop_timeout_s)
            if writer and writer.is_alive() and process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                return_code = process.wait(timeout=self.config.process_stop_timeout_s)
            except subprocess.TimeoutExpired:
                self._stop_process(process, "ffmpeg")
                return_code = process.poll()
            details = "\n".join(self._ffmpeg_stderr[-50:])
            if self._recording_error or return_code not in (0, None) or not temp_path or not temp_path.exists() or temp_path.stat().st_size == 0:
                failed_path = self._move_failed_file(temp_path)
                self._finish_recording_state()
                raise CameraServiceError(
                    "Видео не удалось корректно завершить.",
                    "FFMPEG_ENCODING_FAILED",
                    self._recording_error or details or f"Незавершённый файл: {failed_path}",
                )
            probe = self._probe_video(temp_path)
            if not probe.get("valid"):
                failed_path = self._move_failed_file(temp_path)
                self._finish_recording_state()
                raise CameraServiceError(
                    "FFprobe не подтвердил корректность видео.",
                    "INVALID_OUTPUT_FILE",
                    str(probe.get("details") or failed_path),
                )
            assert final_path is not None
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, final_path)
            record = self._register_file(final_path, "video", duration_s=float(probe.get("duration_s") or 0.0))
            self._finish_recording_state()
            return record

    def list_files(self) -> list[Dict[str, Any]]:
        with self._lock:
            records = sorted(self._files.values(), key=lambda item: item.created_at, reverse=True)
            return [record.public() for record in records]

    def file_path(self, file_id: str) -> tuple[Path, MediaRecord]:
        with self._lock:
            record = self._files.get(file_id)
        if not record:
            raise CameraServiceError("Файл не найден.", "FILE_NOT_FOUND", status_code=404)
        path = Path(record.path).resolve()
        try:
            path.relative_to(self.data_dir)
        except ValueError as exc:
            raise CameraServiceError("Недопустимый путь к файлу.", "FILE_NOT_FOUND", status_code=404) from exc
        if not path.is_file():
            raise CameraServiceError("Файл больше не существует.", "FILE_NOT_FOUND", status_code=404)
        return path, record

    def delete_file(self, file_id: str) -> MediaRecord:
        path, record = self.file_path(file_id)
        with self._lock:
            recording_path = self._recording_final.resolve() if self._recording_final else None
            if recording_path == path:
                raise CameraServiceError("Нельзя удалить файл активной записи.", "CAMERA_BUSY")
        try:
            path.unlink()
        except OSError as exc:
            raise CameraServiceError("Не удалось удалить файл с Raspberry Pi.", "FILE_DELETE_FAILED", str(exc)) from exc
        with self._lock:
            self._files.pop(file_id, None)
        return record

    def wait_for_frame(self, previous_sequence: int, timeout_s: float = 5.0) -> tuple[Optional[bytes], int, bool]:
        deadline = time.monotonic() + timeout_s
        with self._frame_condition:
            while (
                self._frame_sequence == previous_sequence
                and self._process_running(self._gphoto)
                and not self._stream_stop_requested.is_set()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_condition.wait(timeout=remaining)
            frame = self._latest_frame if self._frame_sequence != previous_sequence else None
            active = self._process_running(self._gphoto) and not self._stream_stop_requested.is_set()
            return frame, self._frame_sequence, active

    def liveview_client_connected(self) -> None:
        with self._lock:
            if self._liveview_idle_timer is not None:
                self._liveview_idle_timer.cancel()
                self._liveview_idle_timer = None
            self._liveview_clients += 1

    def liveview_client_disconnected(self) -> None:
        with self._lock:
            self._liveview_clients = max(0, self._liveview_clients - 1)
            if (
                self._liveview_clients == 0
                and self._process_running(self._gphoto)
                and not self._process_running(self._ffmpeg)
            ):
                if self._liveview_idle_timer is not None:
                    self._liveview_idle_timer.cancel()
                timer = threading.Timer(2.0, self._stop_liveview_if_idle)
                timer.name = "liveview-idle-stop"
                timer.daemon = True
                self._liveview_idle_timer = timer
                timer.start()

    def _stop_liveview_if_idle(self) -> None:
        with self._lock:
            should_stop = (
                self._liveview_clients == 0
                and self._process_running(self._gphoto)
                and not self._process_running(self._ffmpeg)
            )
            self._liveview_idle_timer = None
        if not should_stop:
            return
        try:
            LOGGER.info("Stopping idle LiveView after the last client disconnected")
            self.stop_stream()
        except Exception:
            LOGGER.exception("Could not stop idle LiveView")

    def shutdown(self) -> None:
        LOGGER.info("Stopping camera service")
        try:
            if self._process_running(self._ffmpeg):
                self.stop_recording()
            elif self._ffmpeg is not None:
                self._abort_recording_to_failed()
        except Exception:
            LOGGER.exception("Could not finalize recording during shutdown")
            self._abort_recording_to_failed()
        try:
            self.stop_stream(allow_recording=True)
        except Exception:
            LOGGER.exception("Could not stop LiveView during shutdown")

    def _stream_reader_loop(self, process: subprocess.Popen) -> None:
        buffer = bytearray()
        try:
            assert process.stdout is not None
            while not self._stream_stop_requested.is_set():
                chunk = process.stdout.read(16_384)
                if not chunk:
                    break
                buffer.extend(chunk)
                for frame in self._extract_frames(buffer):
                    now_mono = time.monotonic()
                    dimensions = jpeg_dimensions(frame)
                    with self._frame_condition:
                        self._latest_frame = frame
                        if dimensions:
                            self._latest_frame_dimensions = dimensions
                        self._last_frame_at = datetime.now(timezone.utc)
                        self._frame_sequence += 1
                        self._frame_times.append(now_mono)
                        self._first_frame.set()
                        self._frame_condition.notify_all()
                    self._queue_recording_frame(frame)
        except Exception as exc:
            LOGGER.exception("LiveView reader failed")
            with self._lock:
                self.last_error = str(exc)
        finally:
            with self._frame_condition:
                unexpected = not self._stream_stop_requested.is_set()
                if unexpected and self.camera_connected:
                    details = "\n".join(self._gphoto_stderr[-20:])
                    self.last_error = details or "Процесс gphoto2 LiveView завершился."
                    self.state = CameraState.ERROR
                self._frame_condition.notify_all()

    def _record_writer_loop(self, process: subprocess.Popen) -> None:
        try:
            if process.stdin is None:
                raise RuntimeError("stdin ffmpeg недоступен")
            while not self._record_stop_requested.is_set() or not self._record_queue.empty():
                try:
                    frame = self._record_queue.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                process.stdin.write(frame)
                self._recorded_frames += 1
            process.stdin.close()
        except (BrokenPipeError, OSError, RuntimeError) as exc:
            self._recording_error = str(exc)
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass

    def _queue_recording_frame(self, frame: bytes) -> None:
        with self._lock:
            active = self._process_running(self._ffmpeg) and not self._record_stop_requested.is_set()
        if not active:
            return
        try:
            self._record_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._record_queue.get_nowait()
            except queue.Empty:
                pass
            self._dropped_frames += 1
            try:
                self._record_queue.put_nowait(frame)
            except queue.Full:
                self._dropped_frames += 1

    def _probe_video(self, path: Path) -> Dict[str, Any]:
        self._check_binary(self.config.ffprobe_bin, "ffprobe")
        result = self._run_command(
            [
                self.config.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            self.config.command_timeout_s,
            check=False,
        )
        if result.returncode != 0:
            return {"valid": False, "details": result.stderr or result.stdout}
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams") or []
            duration = float((payload.get("format") or {}).get("duration") or 0.0)
            valid = duration > 0 and any(item.get("codec_type") == "video" for item in streams)
            return {"valid": valid, "duration_s": duration, "details": payload}
        except Exception as exc:
            return {"valid": False, "details": str(exc)}

    def _finish_recording_state(self) -> None:
        with self._lock:
            self._ffmpeg = None
            self._record_writer_thread = None
            self._recording_temp = None
            self._recording_final = None
            self._recording_started_at = None
            self._record_stop_requested.clear()
            self._clear_record_queue()
            self.state = CameraState.LIVEVIEW if self._process_running(self._gphoto) else CameraState.READY

    def _abort_recording_to_failed(self) -> None:
        with self._lock:
            process = self._ffmpeg
            self._record_stop_requested.set()
        if self._process_running(process):
            if process and process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            self._stop_process(process, "ffmpeg")
        self._move_failed_file(self._recording_temp)
        self._finish_recording_state()

    def _reset_failed_recording(self) -> None:
        self._move_failed_file(self._recording_temp)
        self._finish_recording_state()

    def _move_failed_file(self, path: Optional[Path]) -> Optional[Path]:
        if not path or not path.exists():
            return None
        target = self.failed_dir / path.name
        if target.exists():
            target = self.failed_dir / f"{path.stem}_{uuid.uuid4().hex[:6]}{path.suffix}"
        os.replace(path, target)
        return target

    def _stop_gphoto_process(self) -> None:
        with self._lock:
            process = self._gphoto
            self._stream_stop_requested.set()
        if self._process_running(process):
            self._stop_process(process, "gphoto2")
        with self._lock:
            self._gphoto = None

    def _stop_process(self, process: Optional[subprocess.Popen], label: str) -> None:
        if not self._process_running(process):
            return
        assert process is not None
        LOGGER.info("Stopping %s PID %s", label, process.pid)
        try:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=self.config.process_stop_timeout_s)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            process.terminate()
            process.wait(timeout=3.0)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        process.kill()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            LOGGER.error("%s PID %s did not exit after kill", label, process.pid)

    def _run_command(self, command: list[str], timeout_s: float, check: bool = True) -> subprocess.CompletedProcess:
        LOGGER.info("Command: %s", command)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CameraServiceError("Команда камеры превысила тайм-аут.", "GPHOTO_TIMEOUT", str(exc)) from exc
        except OSError as exc:
            raise CameraServiceError("Не удалось запустить внешнюю команду.", "GPHOTO_PROCESS_FAILED", str(exc)) from exc
        if check and result.returncode != 0:
            raise CameraServiceError(
                "Внешняя команда завершилась с ошибкой.",
                "GPHOTO_PROCESS_FAILED",
                (result.stderr or result.stdout).strip(),
            )
        return result

    def _cleanup_gvfs(self, broad: bool) -> None:
        patterns = ["gvfsd-gphoto2", "gvfs-gphoto2-volume-monitor"]
        if broad:
            patterns.append("gvfs")
        for pattern in patterns:
            try:
                subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=3.0, check=False)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.warning("Could not run pkill for %s", pattern)

    def _register_file(self, path: Path, kind: str, duration_s: Optional[float] = None) -> MediaRecord:
        resolved = path.resolve()
        relative = resolved.relative_to(self.data_dir).as_posix()
        file_id = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
        record = MediaRecord(
            file_id=file_id,
            name=path.name,
            kind=kind,
            path=str(resolved),
            size=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            sha256=self._sha256(path),
            duration_s=duration_s,
        )
        with self._lock:
            self._files[file_id] = record
        return record

    def _index_existing_files(self) -> None:
        for folder, kind in ((self.photos_dir, "photo"), (self.videos_dir, "video")):
            for path in folder.iterdir():
                if path.is_file():
                    try:
                        self._register_file(path, kind)
                    except OSError:
                        LOGGER.exception("Could not index %s", path)

    def _current_fps_locked(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        elapsed = self._frame_times[-1] - self._frame_times[0]
        return (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0.0

    def _clear_record_queue(self) -> None:
        while True:
            try:
                self._record_queue.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    def _extract_frames(buffer: bytearray) -> Iterable[bytes]:
        while True:
            start = buffer.find(JPEG_START)
            if start < 0:
                if len(buffer) > 1:
                    del buffer[:-1]
                return
            if start:
                del buffer[:start]
            end = buffer.find(JPEG_END, 2)
            if end < 0:
                if len(buffer) > 20 * 1024 * 1024:
                    del buffer[:-2]
                return
            end += 2
            frame = bytes(buffer[:end])
            del buffer[:end]
            if len(frame) >= 128:
                yield frame

    @staticmethod
    def _collect_stderr(process: subprocess.Popen, target: list[str], label: str) -> None:
        try:
            if process.stderr is None:
                return
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    target.append(line)
                    if len(target) > 500:
                        del target[:100]
                    LOGGER.debug("%s: %s", label, line)
        except Exception:
            LOGGER.exception("Could not read %s stderr", label)

    @staticmethod
    def _process_running(process: Optional[subprocess.Popen]) -> bool:
        return process is not None and process.poll() is None

    @staticmethod
    def _camera_busy(text: str) -> bool:
        value = (text or "").lower()
        return "busy" in value or "claim the usb device" in value or "could not claim" in value

    @staticmethod
    def _camera_missing(text: str) -> bool:
        value = (text or "").lower()
        return "no camera found" in value or "no cameras found" in value or "could not detect any camera" in value

    @staticmethod
    def _parse_camera_model(detect_output: str, summary_output: str) -> str:
        for line in summary_output.splitlines():
            match = re.match(r"\s*(?:Model|Модель)\s*:\s*(.+)", line, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        for line in detect_output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("model") or set(stripped) == {"-"}:
                continue
            parts = re.split(r"\s{2,}", stripped, maxsplit=1)
            if parts and "usb:" in stripped.lower():
                return parts[0].strip()
        return ""

    @staticmethod
    def _check_binary(binary: str, label: str) -> None:
        if Path(binary).is_file() or shutil.which(binary):
            return
        raise CameraServiceError(f"На Raspberry Pi не найден {label}.", "DEPENDENCY_NOT_FOUND", binary)

    @staticmethod
    def _timestamped_name(prefix: str, suffix: str) -> str:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return f"{prefix}_{stamp}{suffix}"

    def _photo_output_path(self, requested_name: str, default_prefix: str) -> Path:
        stem = safe_capture_stem(requested_name)
        if str(requested_name or "").strip() and not stem:
            raise CameraServiceError(
                "Название снимка должно содержать буквы или цифры.",
                "INVALID_FILE_NAME",
                status_code=400,
            )
        name = f"{stem}.jpg" if stem else self._timestamped_name(default_prefix, ".jpg")
        path = self.photos_dir / name
        if not path.exists():
            return path
        index = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    def _crop_image_in_place(self, path: Path, crop: Optional[Dict[str, Any]]) -> None:
        crop_filter = center_crop_filter(crop)
        if not crop_filter:
            return
        self._check_binary(self.config.ffmpeg_bin, "ffmpeg")
        temporary = self.temporary_dir / f"{path.stem}_{uuid.uuid4().hex}.crop.jpg"
        command = [
            self.config.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            crop_filter,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(temporary),
        ]
        try:
            result = self._run_command(command, self.config.photo_timeout_s, check=False)
            if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
                details = (result.stderr or result.stdout).strip()
                raise CameraServiceError(
                    "Не удалось применить кадрирование к изображению.",
                    "CROP_FAILED",
                    details,
                )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    class _OperationContext:
        def __init__(self, lock: threading.Lock, message: str):
            self.lock = lock
            self.message = message

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                raise CameraServiceError(self.message, "CAMERA_BUSY")
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.lock.release()
            return False

    def _operation(self, message: str) -> "CameraController._OperationContext":
        return self._OperationContext(self._operation_lock, message)


def create_app(config: ServiceConfig) -> FastAPI:
    controller = CameraController(config)
    app = FastAPI(title="OLED Canon Camera Service", version="1.8.6")
    app.state.camera = controller

    @app.exception_handler(CameraServiceError)
    async def camera_error_handler(_request: Request, exc: CameraServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error_code": exc.error_code,
                "message": str(exc),
                "details": exc.details,
            },
        )

    @app.on_event("startup")
    async def startup() -> None:
        if config.auto_initialize:
            try:
                await run_in_threadpool(controller.initialize)
            except Exception:
                LOGGER.exception("Automatic camera initialization failed")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await run_in_threadpool(controller.shutdown)

    @app.get("/api/health")
    async def health():
        return controller.health()

    @app.get("/api/camera/status")
    async def status():
        return controller.status()

    @app.post("/api/camera/initialize")
    async def initialize():
        return await run_in_threadpool(controller.initialize)

    @app.get("/api/camera/capabilities")
    async def capabilities():
        return controller.capabilities()

    @app.post("/api/liveview/start")
    async def start_liveview(payload: Optional[Dict[str, Any]] = Body(default=None)):
        video_settings = (payload or {}).get("video_settings") or {}
        if not isinstance(video_settings, dict):
            raise CameraServiceError("Некорректные параметры LiveView.", "INVALID_REQUEST", status_code=400)
        return await run_in_threadpool(controller.start_stream, video_settings)

    @app.post("/api/liveview/stop")
    async def stop_liveview():
        return await run_in_threadpool(controller.stop_stream)

    @app.get("/api/liveview/stream")
    async def liveview_stream(request: Request):
        boundary = b"frame"

        async def frames():
            sequence = -1
            controller.liveview_client_connected()
            try:
                while not await request.is_disconnected():
                    frame, sequence, active = await run_in_threadpool(controller.wait_for_frame, sequence, 1.0)
                    if frame is not None:
                        yield (
                            b"--" + boundary + b"\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                            + frame
                            + b"\r\n"
                        )
                    if not active:
                        break
            finally:
                controller.liveview_client_disconnected()

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/liveview/snapshot")
    async def snapshot(payload: Optional[Dict[str, Any]] = Body(default=None)):
        request_data = payload or {}
        file_name = str(request_data.get("file_name") or "")
        crop = normalize_center_crop(request_data.get("crop"))
        record = await run_in_threadpool(controller.snapshot, file_name, crop)
        return {"success": True, "file": record.public(), "status": controller.status()}

    @app.post("/api/photo/capture")
    async def capture_photo(payload: Optional[Dict[str, Any]] = Body(default=None)):
        photo_settings = (payload or {}).get("photo_settings") or {}
        file_name = str((payload or {}).get("file_name") or "")
        crop = normalize_center_crop((payload or {}).get("crop"))
        if not isinstance(photo_settings, dict):
            raise CameraServiceError("Некорректные параметры качества фото.", "INVALID_REQUEST", status_code=400)
        record = await run_in_threadpool(controller.capture_photo, photo_settings, file_name, crop)
        return {"success": True, "file": record.public(), "status": controller.status()}

    @app.post("/api/video/start")
    async def start_video(payload: Optional[Dict[str, Any]] = Body(default=None)):
        request_data = payload or {}
        video_settings = request_data.get("video_settings") or {}
        crop = normalize_center_crop(request_data.get("crop"))
        if not isinstance(video_settings, dict):
            raise CameraServiceError("Некорректные параметры видео.", "INVALID_REQUEST", status_code=400)
        return await run_in_threadpool(controller.start_recording, video_settings, crop)

    @app.post("/api/video/stop")
    async def stop_video():
        record = await run_in_threadpool(controller.stop_recording)
        return {"success": True, "file": record.public(), "status": controller.status()}

    @app.get("/api/files")
    async def files():
        return {"success": True, "files": controller.list_files()}

    @app.get("/api/files/{file_id}")
    async def download(file_id: str):
        path, record = controller.file_path(file_id)
        return FileResponse(path, filename=record.name, media_type="application/octet-stream")

    @app.delete("/api/files/{file_id}")
    async def delete_file(file_id: str):
        record = await run_in_threadpool(controller.delete_file, file_id)
        return {"success": True, "deleted": record.public()}

    return app


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / "camera_service.log", encoding="utf-8")],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OLED Canon camera service on Raspberry Pi.")
    parser.add_argument("--config", type=Path, help="Path to JSON config. Defaults to built-in settings.")
    parser.add_argument("--host", help="Override listen host.")
    parser.add_argument("--port", type=int, help="Override listen port.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServiceConfig.load(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    data_dir = Path(config.data_dir).expanduser().resolve()
    configure_logging(data_dir / "logs")
    import uvicorn

    uvicorn.run(create_app(config), host=config.host, port=int(config.port), log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
