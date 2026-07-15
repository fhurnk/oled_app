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

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool


LOGGER = logging.getLogger("oled_camera_service")
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


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

        self._ffmpeg: Optional[subprocess.Popen] = None
        self._ffmpeg_stderr: list[str] = []
        self._record_writer_thread: Optional[threading.Thread] = None
        self._recording_temp: Optional[Path] = None
        self._recording_final: Optional[Path] = None
        self._recording_started_at: Optional[datetime] = None
        self._recorded_frames = 0
        self._dropped_frames = 0
        self._recording_error: Optional[str] = None

        self._files: Dict[str, MediaRecord] = {}
        self._index_existing_files()

    def health(self) -> Dict[str, Any]:
        return {"success": True, "service": "camera", "status": "ok", "version": "1.8.0-alpha"}

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
            return {
                "success": True,
                "camera_connected": self.camera_connected,
                "camera_model": self.camera_model or None,
                "state": self.state.value,
                "liveview_active": stream_active,
                "recording_active": recording_active,
                "last_frame_at": self._iso(self._last_frame_at),
                "fps": round(fps, 2),
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
            with self._lock:
                self.camera_connected = True
                self.camera_model = model or "Canon / gPhoto2 camera"
                self.state = CameraState.READY
                self.last_error = None
            LOGGER.info("Camera initialized: %s", self.camera_model)
            return self.status()

    def start_stream(self) -> Dict[str, Any]:
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
        with self._lock:
            if self._process_running(self._ffmpeg) and not allow_recording:
                raise CameraServiceError("Сначала остановите запись видео.", "CAMERA_BUSY")
            process = self._gphoto
            if not self._process_running(process):
                self._gphoto = None
                if self.camera_connected and self.state not in {CameraState.CAPTURING_PHOTO, CameraState.STOPPING}:
                    self.state = CameraState.READY
                return self.status()
            self.state = CameraState.STOPPING
            self._stream_stop_requested.set()
        self._stop_process(process, "gphoto2")
        reader = self._reader_thread
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        with self._lock:
            self._gphoto = None
            self._reader_thread = None
            self._latest_frame = None
            self._frame_condition.notify_all()
            self.state = CameraState.READY if self.camera_connected else CameraState.DISCONNECTED
        return self.status()

    def snapshot(self) -> MediaRecord:
        with self._operation("Камера уже выполняет другую операцию."):
            with self._lock:
                frame = self._latest_frame
            if not frame:
                raise CameraServiceError("Сначала запустите LiveView и дождитесь кадра.", "INVALID_STATE")
            path = self.photos_dir / self._timestamped_name("camera_preview", ".jpg")
            path.write_bytes(frame)
            return self._register_file(path, "preview")

    def capture_photo(self) -> MediaRecord:
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
            path = self.photos_dir / self._timestamped_name("camera_photo", ".jpg")
            command = [
                self.config.gphoto2_bin,
                "--filename",
                str(path),
                "--capture-image-and-download",
            ]
            try:
                result = self._run_command(command, self.config.photo_timeout_s, check=False)
                if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
                    details = (result.stderr or result.stdout).strip()
                    raise CameraServiceError("Не удалось сделать фото камерой.", "PHOTO_CAPTURE_FAILED", details)
                record = self._register_file(path, "photo")
                with self._lock:
                    self.state = CameraState.READY
                    self.last_error = None
            except Exception as exc:
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

    def start_recording(self) -> Dict[str, Any]:
        with self._lock:
            already_streaming = self._process_running(self._gphoto)
        if not already_streaming:
            self.start_stream()
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
                    "-c:v",
                    str(self.config.ffmpeg_codec),
                    "-preset",
                    str(self.config.ffmpeg_preset),
                    "-crf",
                    str(int(self.config.ffmpeg_crf)),
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "vfr",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(temp_path),
                ]
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

    def wait_for_frame(self, previous_sequence: int, timeout_s: float = 5.0) -> tuple[Optional[bytes], int, bool]:
        deadline = time.monotonic() + timeout_s
        with self._frame_condition:
            while self._frame_sequence == previous_sequence and self._process_running(self._gphoto):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_condition.wait(timeout=remaining)
            frame = self._latest_frame if self._frame_sequence != previous_sequence else None
            return frame, self._frame_sequence, self._process_running(self._gphoto)

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
                    with self._frame_condition:
                        self._latest_frame = frame
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
    app = FastAPI(title="OLED Canon Camera Service", version="1.8.0-alpha")
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

    @app.post("/api/liveview/start")
    async def start_liveview():
        return await run_in_threadpool(controller.start_stream)

    @app.post("/api/liveview/stop")
    async def stop_liveview():
        return await run_in_threadpool(controller.stop_stream)

    @app.get("/api/liveview/stream")
    async def liveview_stream():
        boundary = b"frame"

        async def frames():
            sequence = -1
            while True:
                frame, sequence, active = await run_in_threadpool(controller.wait_for_frame, sequence, 5.0)
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

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/liveview/snapshot")
    async def snapshot():
        record = await run_in_threadpool(controller.snapshot)
        return {"success": True, "file": record.public(), "status": controller.status()}

    @app.post("/api/photo/capture")
    async def capture_photo():
        record = await run_in_threadpool(controller.capture_photo)
        return {"success": True, "file": record.public(), "status": controller.status()}

    @app.post("/api/video/start")
    async def start_video():
        return await run_in_threadpool(controller.start_recording)

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
