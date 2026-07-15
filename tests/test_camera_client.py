from __future__ import annotations

import threading
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from oled_app.camera.client import (
    CameraClient,
    available_path,
    build_camera_service_url,
    extract_jpeg_frames,
    safe_local_filename,
)
from oled_app.gui.camera_window import decode_liveview_frame


class CameraClientHelpersTests(unittest.TestCase):
    def test_extracts_multiple_jpegs_and_keeps_partial_tail(self) -> None:
        first = b"\xff\xd8" + b"a" * 128 + b"\xff\xd9"
        second = b"\xff\xd8" + b"b" * 128 + b"\xff\xd9"
        buffer = bytearray(b"multipart header\r\n" + first + second + b"\xff\xd8partial")

        self.assertEqual(list(extract_jpeg_frames(buffer)), [first, second])
        self.assertEqual(buffer, bytearray(b"\xff\xd8partial"))

    def test_url_builder_accepts_plain_host_and_existing_scheme(self) -> None:
        self.assertEqual(build_camera_service_url("192.168.4.1", 8765), "http://192.168.4.1:8765")
        self.assertEqual(build_camera_service_url("http://raspberrypi.local:9000/", 8765), "http://raspberrypi.local:9000")

    def test_filename_is_reduced_to_safe_basename(self) -> None:
        self.assertEqual(safe_local_filename("../bad:name?.jpg"), "bad_name_.jpg")

    def test_liveview_frame_is_fully_loaded_and_resized(self) -> None:
        source = Image.new("RGB", (1024, 680), "red")
        encoded = BytesIO()
        source.save(encoded, format="JPEG")

        decoded = decode_liveview_frame(encoded.getvalue(), (320, 240))

        self.assertEqual(decoded.mode, "RGB")
        self.assertEqual(decoded.size, (320, 213))
        self.assertIsNone(getattr(decoded, "fp", None))

    def test_active_liveview_response_can_be_closed_from_another_thread(self) -> None:
        response = BlockingResponse()
        client = CameraClient("http://camera.test:8765")
        stop_event = threading.Event()
        failures: list[Exception] = []

        def receive() -> None:
            try:
                client.iter_liveview_frames(stop_event, lambda _frame: None)
            except Exception as exc:  # pragma: no cover - assertion reports the actual failure
                failures.append(exc)

        with patch("urllib.request.urlopen", return_value=response):
            thread = threading.Thread(target=receive)
            thread.start()
            self.assertTrue(response.read_started.wait(1.0))
            stop_event.set()
            client.close_liveview_stream()
            thread.join(1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        client.close_liveview_stream()


class BlockingResponse:
    def __init__(self) -> None:
        self.read_started = threading.Event()
        self.closed = threading.Event()

    def read(self, _size: int) -> bytes:
        self.read_started.set()
        self.closed.wait(2.0)
        raise OSError("stream closed")

    def close(self) -> None:
        self.closed.set()


if __name__ == "__main__":
    unittest.main()
