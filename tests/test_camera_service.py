from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from raspberry_camera_service.camera_service import (
    CameraController,
    ServiceConfig,
    center_crop_filter,
    jpeg_dimensions,
    normalize_center_crop,
    parse_gphoto_config,
    safe_capture_stem,
)


class CameraServiceQualityTests(unittest.TestCase):
    def test_center_crop_is_validated_and_translated_to_ffmpeg(self) -> None:
        crop = normalize_center_crop({"width_percent": 72.5, "height_percent": 80})

        self.assertEqual(crop, {"width_percent": 72.5, "height_percent": 80.0})
        self.assertEqual(
            center_crop_filter(crop),
            "crop=trunc(iw*0.725000/2)*2:trunc(ih*0.800000/2)*2:(iw-ow)/2:(ih-oh)/2",
        )
        self.assertEqual(center_crop_filter(), "")

    def test_still_image_crop_replaces_source_with_ffmpeg_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            source = Path(folder) / "photos" / "preview.jpg"
            source.write_bytes(b"original")

            def create_output(command, _timeout, check=False):
                Path(command[-1]).write_bytes(b"cropped")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(controller, "_check_binary"), patch.object(
                controller, "_run_command", side_effect=create_output
            ) as run_command:
                controller._crop_image_in_place(source, {"width_percent": 70, "height_percent": 80})

            cropped_bytes = source.read_bytes()
            command = run_command.call_args.args[0]

        self.assertEqual(cropped_bytes, b"cropped")
        self.assertIn("-vf", command)
        self.assertIn("iw*0.700000", command[command.index("-vf") + 1])

    def test_stop_stream_wakes_waiting_liveview_clients_immediately(self) -> None:
        class RunningProcess:
            stdout = None
            stderr = None

            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            controller.camera_connected = True
            controller._gphoto = RunningProcess()
            result = []
            waiter = threading.Thread(target=lambda: result.append(controller.wait_for_frame(0, 5.0)))
            waiter.start()
            time.sleep(0.05)
            with patch.object(controller, "_stop_process"):
                controller.stop_stream()
            waiter.join(0.5)

        self.assertFalse(waiter.is_alive())
        self.assertEqual(result, [(None, 0, False)])

    def test_idle_liveview_is_stopped_after_last_client_disconnects(self) -> None:
        class RunningProcess:
            @staticmethod
            def poll():
                return None

        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            controller._gphoto = RunningProcess()
            controller.liveview_client_connected()
            controller.liveview_client_disconnected()
            timer = controller._liveview_idle_timer
            self.assertIsNotNone(timer)
            assert timer is not None
            timer.cancel()
            with patch.object(controller, "stop_stream") as stop_stream:
                controller._stop_liveview_if_idle()

        stop_stream.assert_called_once_with()

    def test_custom_capture_name_is_sanitized_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            first = controller._photo_output_path("Образец: 12.jpg", "camera_photo")
            first.write_bytes(b"first")
            second = controller._photo_output_path("Образец: 12.jpg", "camera_photo")

        self.assertEqual(first.name, "Образец_ 12.jpg")
        self.assertEqual(second.name, "Образец_ 12_2.jpg")
        self.assertEqual(safe_capture_stem("../bad?.jpeg"), "bad")

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

    def test_exposure_discovery_uses_only_writable_camera_choices(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            responses = [
                subprocess.CompletedProcess(
                    [],
                    0,
                    (
                        "/main/capturesettings/aperture\n"
                        "/main/imgsettings/iso\n"
                        "/main/capturesettings/shutterspeed\n"
                        "/main/capturesettings/exposurecompensation\n"
                    ),
                    "",
                ),
                subprocess.CompletedProcess([], 0, "Current: 100\nChoice: 0 100\nChoice: 1 400\n", ""),
                subprocess.CompletedProcess([], 0, "Current: 1/30\nChoice: 0 1/30\nChoice: 1 1/4\n", ""),
                subprocess.CompletedProcess([], 0, "Readonly: 1\nCurrent: 4\nChoice: 0 4\nChoice: 1 5.6\n", ""),
                subprocess.CompletedProcess([], 0, "Current: 0\nChoice: 0 -1\nChoice: 1 0\nChoice: 2 +1\n", ""),
            ]
            with patch.object(controller, "_run_command", side_effect=responses):
                controls = controller._discover_exposure_controls()

        self.assertEqual([control["key"] for control in controls], ["iso", "shutterspeed", "exposurecompensation"])
        self.assertEqual(controls[0]["label"], "ISO")
        self.assertEqual(controls[1]["label"], "Выдержка")
        self.assertEqual(controls[1]["choices"], ["1/30", "1/4"])

    def test_exposure_setting_is_applied_verified_and_cached(self) -> None:
        path = "/main/imgsettings/iso"
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            controller._exposure_controls = {
                path: {
                    "path": path,
                    "key": "iso",
                    "label": "ISO",
                    "current": "100",
                    "choices": ["100", "400"],
                }
            }
            responses = [
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "Current: 400\nChoice: 0 100\nChoice: 1 400\n", ""),
            ]
            with patch.object(controller, "_run_command", side_effect=responses) as run_command:
                controller._apply_photo_settings({path: "400"})

        self.assertEqual(
            run_command.call_args_list[0].args[0],
            ["gphoto2", "--set-config-value", f"{path}=400"],
        )
        self.assertEqual(controller._exposure_controls[path]["current"], "400")

    def test_video_discovery_uses_only_camera_reported_quality_and_fps(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            responses = [
                subprocess.CompletedProcess(
                    [],
                    0,
                    "/main/actions/liveviewsize\n/main/capturesettings/moviefps\n/main/other/fps\n",
                    "",
                ),
                subprocess.CompletedProcess([], 0, "Current: Large\nChoice: 0 Small\nChoice: 1 Large\n", ""),
                subprocess.CompletedProcess([], 0, "Current: 25\nChoice: 0 24\nChoice: 1 25\n", ""),
            ]
            with patch.object(controller, "_run_command", side_effect=responses):
                controls = controller._discover_video_controls()

        self.assertEqual([control["role"] for control in controls], ["quality", "fps"])
        self.assertEqual(controls[0]["choices"], ["Small", "Large"])
        self.assertEqual(controls[1]["choices"], ["24", "25"])

    def test_viewfinder_control_is_discovered_and_verified_off(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            discovery = subprocess.CompletedProcess(
                [],
                0,
                "/main/actions/viewfinder\n/main/actions/liveviewsize\n",
                "",
            )
            quality = subprocess.CompletedProcess([], 0, "Current: Large\nChoice: 0 Small\nChoice: 1 Large\n", "")
            with patch.object(controller, "_run_command", side_effect=[discovery, quality]):
                controller._discover_video_controls()

            applied = subprocess.CompletedProcess([], 0, "", "")
            verified = subprocess.CompletedProcess([], 0, "Current: 0\n", "")
            with patch.object(controller, "_run_command", side_effect=[applied, verified]) as command:
                result = controller._disable_camera_viewfinder()

        self.assertEqual(controller._viewfinder_control_path, "/main/actions/viewfinder")
        self.assertTrue(result)
        self.assertEqual(
            command.call_args_list[0].args[0],
            ["gphoto2", "--set-config-value", "/main/actions/viewfinder=0"],
        )

    def test_video_discovery_does_not_invent_fps_when_camera_has_no_control(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            controller = CameraController(ServiceConfig(data_dir=folder))
            responses = [
                subprocess.CompletedProcess([], 0, "/main/actions/liveviewsize\n", ""),
                subprocess.CompletedProcess([], 0, "Current: Large\nChoice: 0 Small\nChoice: 1 Large\n", ""),
            ]
            with patch.object(controller, "_run_command", side_effect=responses):
                controls = controller._discover_video_controls()

        self.assertEqual([control["role"] for control in controls], ["quality"])

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
