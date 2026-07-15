from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from oled_app.camera.client import (
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


if __name__ == "__main__":
    unittest.main()
