from __future__ import annotations

import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from raspberry_camera_service.camera_service import (
    CameraController,
    ServiceConfig,
    jpeg_dimensions,
    parse_gphoto_config,
)


class CameraServiceQualityTests(unittest.TestCase):
    def test_parse_gphoto_config_choices(self) -> None:
        parsed = parse_gphoto_config(
            "/main/imgsettings/imageformat",
            """Label: Image Format
Readonly: 0
Type: RADIO
Current: Large Fine JPEG
Choice: 0 Large Fine JPEG
Choice: 1 RAW
Choice: 2 RAW + Large Fine JPEG
""",
        )

        self.assertEqual(parsed["current"], "Large Fine JPEG")
        self.assertEqual(parsed["choices"], ["Large Fine JPEG", "RAW", "RAW + Large Fine JPEG"])
        self.assertFalse(parsed["readonly"])

    def test_jpeg_dimensions_reads_real_frame(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (1024, 680), "black").save(buffer, format="JPEG")
        self.assertEqual(jpeg_dimensions(buffer.getvalue()), (1024, 680))

    def test_discovery_filters_raw_formats(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            responses = [
                subprocess.CompletedProcess([], 0, "/main/imgsettings/imageformat\n/main/imgsettings/imagesize\n", ""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    "Current: Large Fine JPEG\nChoice: 0 Large Fine JPEG\nChoice: 1 RAW\nChoice: 2 Medium Fine JPEG\n",
                    "",
                ),
                subprocess.CompletedProcess([], 0, "Current: Large\nChoice: 0 Large\nChoice: 1 Medium\n", ""),
            ]
            with patch.object(controller, "_run_command", side_effect=responses):
                controls = controller._discover_photo_controls()

        self.assertEqual(controls[0]["choices"], ["Large Fine JPEG", "Medium Fine JPEG"])
        self.assertEqual(controls[1]["choices"], ["Large", "Medium"])

    def test_delete_file_removes_only_registered_media(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            path = Path(folder) / "photos" / "photo.jpg"
            path.write_bytes(b"photo")
            record = controller._register_file(path, "photo")

            deleted = controller.delete_file(record.file_id)

            self.assertEqual(deleted.file_id, record.file_id)
            self.assertFalse(path.exists())
            self.assertEqual(controller.list_files(), [])


if __name__ == "__main__":
    unittest.main()
