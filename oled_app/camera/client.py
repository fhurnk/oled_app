"""HTTP client for the alpha Raspberry Pi camera service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


class CameraClientError(RuntimeError):
    """A user-facing camera service or network error."""

    def __init__(self, message: str, error_code: str = "NETWORK_ERROR", details: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.details = details


@dataclass(frozen=True)
class RemoteFile:
    """Metadata for one file stored by the Raspberry Pi service."""

    file_id: str
    name: str
    kind: str
    size: int
    created_at: str = ""
    sha256: str = ""

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "RemoteFile":
        return cls(
            file_id=str(value.get("file_id") or ""),
            name=str(value.get("name") or "camera_file"),
            kind=str(value.get("kind") or "unknown"),
            size=int(value.get("size") or 0),
            created_at=str(value.get("created_at") or ""),
            sha256=str(value.get("sha256") or ""),
        )


def build_camera_service_url(host: str, port: int | str) -> str:
    """Build a normalized base URL from a host/IP and port."""

    value = str(host or "").strip().rstrip("/")
    if not value:
        value = "192.168.4.1"
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname or "192.168.4.1"
    scheme = parsed.scheme or "http"
    selected_port = parsed.port or int(port)
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{scheme}://{hostname}:{selected_port}"


class CameraClient:
    """Small dependency-free client used by the Tk camera test window."""

    def __init__(self, base_url: str, timeout_s: float = 8.0, stream_timeout_s: float = 12.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = max(float(timeout_s), 0.5)
        self.stream_timeout_s = max(float(stream_timeout_s), 1.0)
        self._stream_response_lock = threading.Lock()
        self._stream_response = None

    def health(self) -> Dict[str, Any]:
        return self._json_request("GET", "/api/health")

    def status(self) -> Dict[str, Any]:
        return self._json_request("GET", "/api/camera/status")

    def initialize(self) -> Dict[str, Any]:
        return self._json_request("POST", "/api/camera/initialize")

    def start_liveview(self) -> Dict[str, Any]:
        return self._json_request("POST", "/api/liveview/start")

    def stop_liveview(self) -> Dict[str, Any]:
        return self._json_request("POST", "/api/liveview/stop")

    def save_liveview_snapshot(self) -> RemoteFile:
        return self._file_from_action("/api/liveview/snapshot")

    def capture_photo(self) -> RemoteFile:
        return self._file_from_action("/api/photo/capture")

    def start_recording(self) -> Dict[str, Any]:
        return self._json_request("POST", "/api/video/start")

    def stop_recording(self) -> RemoteFile:
        return self._file_from_action("/api/video/stop")

    def list_files(self) -> list[RemoteFile]:
        payload = self._json_request("GET", "/api/files")
        return [RemoteFile.from_dict(item) for item in payload.get("files", [])]

    def iter_liveview_frames(
        self,
        stop_event: threading.Event,
        on_frame: Callable[[bytes], None],
    ) -> None:
        """Read an MJPEG response and deliver complete JPEG frames."""

        request = urllib.request.Request(
            self.base_url + "/api/liveview/stream",
            headers={"Accept": "multipart/x-mixed-replace"},
            method="GET",
        )
        buffer = bytearray()
        response = None
        try:
            response = urllib.request.urlopen(request, timeout=self.stream_timeout_s)
            with self._stream_response_lock:
                if stop_event.is_set():
                    response.close()
                    return
                self._stream_response = response
            while not stop_event.is_set():
                if not self._wait_for_stream_data(response, stop_event):
                    return
                reader = getattr(response, "read1", response.read)
                chunk = reader(16_384)
                if not chunk:
                    if stop_event.is_set():
                        return
                    raise CameraClientError("Поток LiveView был закрыт сервисом.", "LIVEVIEW_STREAM_LOST")
                buffer.extend(chunk)
                for frame in extract_jpeg_frames(buffer):
                    if stop_event.is_set():
                        return
                    on_frame(frame)
        except CameraClientError:
            raise
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if not stop_event.is_set():
                raise CameraClientError(
                    "Соединение с LiveView потеряно.",
                    "NETWORK_ERROR",
                    str(exc),
                ) from exc
        finally:
            with self._stream_response_lock:
                if self._stream_response is response:
                    self._stream_response = None
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass

    def close_liveview_stream(self) -> None:
        """Interrupt an active LiveView read from another thread."""

        with self._stream_response_lock:
            response = self._stream_response
        if response is not None:
            self._shutdown_stream_socket(response)
            try:
                response.close()
            except OSError:
                pass

    @staticmethod
    def _shutdown_stream_socket(response: Any) -> None:
        """Unblock HTTPResponse.read before closing it on Windows."""

        buffered = getattr(response, "fp", None)
        raw = getattr(buffered, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    @staticmethod
    def _wait_for_stream_data(response: Any, stop_event: threading.Event) -> bool:
        """Poll the stream socket so cancellation never waits on a long read."""

        buffered = getattr(response, "fp", None)
        raw = getattr(buffered, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is None:
            return not stop_event.is_set()
        while not stop_event.is_set():
            try:
                readable, _, _ = select.select([sock], [], [], 0.2)
            except (OSError, ValueError):
                return True
            if readable:
                return True
        return False

    def download_file(self, remote_file: RemoteFile, output_dir: Path | str) -> Path:
        """Download through .part, then verify size and optional SHA-256."""

        folder = Path(output_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        target = available_path(folder / safe_local_filename(remote_file.name))
        temporary = target.with_name(target.name + ".part")
        url = self.base_url + "/api/files/" + urllib.parse.quote(remote_file.file_id, safe="")
        digest = hashlib.sha256()
        received = 0
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout_s, 30.0)) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            temporary.unlink(missing_ok=True)
            raise CameraClientError("Не удалось скачать файл с Raspberry Pi.", details=str(exc)) from exc

        if remote_file.size and received != remote_file.size:
            temporary.unlink(missing_ok=True)
            raise CameraClientError(
                f"Размер скачанного файла не совпал: {received} вместо {remote_file.size} байт.",
                "INVALID_OUTPUT_FILE",
            )
        if remote_file.sha256 and digest.hexdigest().lower() != remote_file.sha256.lower():
            temporary.unlink(missing_ok=True)
            raise CameraClientError("Контрольная сумма скачанного файла не совпала.", "INVALID_OUTPUT_FILE")
        os.replace(temporary, target)
        return target

    def _file_from_action(self, path: str) -> RemoteFile:
        payload = self._json_request("POST", path)
        file_payload = payload.get("file")
        if not isinstance(file_payload, dict):
            raise CameraClientError("Сервис не вернул сведения о созданном файле.", "INVALID_OUTPUT_FILE")
        return RemoteFile.from_dict(file_payload)

    def _json_request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CameraClientError(
                f"Нет соединения с сервисом камеры {self.base_url}.",
                "NETWORK_ERROR",
                str(exc),
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise CameraClientError("Сервис камеры вернул некорректный ответ.", "INVALID_RESPONSE", str(exc)) from exc
        if not isinstance(value, dict):
            raise CameraClientError("Сервис камеры вернул некорректный ответ.", "INVALID_RESPONSE")
        if value.get("success") is False:
            raise CameraClientError(
                str(value.get("message") or "Операция с камерой завершилась ошибкой."),
                str(value.get("error_code") or "CAMERA_ERROR"),
                str(value.get("details") or ""),
            )
        return value

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> CameraClientError:
        raw = exc.read()
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception:
            value = {}
        return CameraClientError(
            str(value.get("message") or f"Сервис камеры вернул HTTP {exc.code}."),
            str(value.get("error_code") or f"HTTP_{exc.code}"),
            str(value.get("details") or raw.decode("utf-8", errors="replace")),
        )


def extract_jpeg_frames(buffer: bytearray) -> Iterable[bytes]:
    """Remove and yield complete JPEG images from a mutable stream buffer."""

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


def safe_local_filename(name: str) -> str:
    value = Path(str(name or "camera_file")).name
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value).strip(" .")
    return value or "camera_file"


def available_path(path: Path) -> Path:
    if not path.exists() and not path.with_name(path.name + ".part").exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists() and not candidate.with_name(candidate.name + ".part").exists():
            return candidate
    raise CameraClientError("Не удалось подобрать свободное имя локального файла.", "INVALID_OUTPUT_FILE")
