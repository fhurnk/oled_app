from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

from oled_app.camera.client import RemoteFile
from oled_v2.camera import CameraController


class FakeCameraClient:
    instances: list["FakeCameraClient"] = []

    def __init__(self, base_url: str, timeout_s: float, stream_timeout_s: float):
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.stream_timeout_s = stream_timeout_s
        self.initialize_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.stream_closed = threading.Event()
        self.files = [RemoteFile("file-1", "preview.jpg", "photo", 321, "2026-09-28T10:00:00Z", "abc")]
        self.__class__.instances.append(self)

    def health(self):
        return {"status": "ok", "service": "oled-camera"}

    def initialize(self):
        self.initialize_calls += 1
        return {"success": True}

    def status(self):
        return {"model": "Fake Canon", "liveview_active": self.start_calls > self.stop_calls}

    def capabilities(self):
        return {
            "photo_controls": [{
                "path": "/main/imgsettings/imageformat", "label": "JPEG",
                "current": "Large Fine JPEG", "choices": ["Large Fine JPEG", "Medium JPEG"],
            }],
            "exposure_controls": [{
                "path": "/main/imgsettings/iso", "label": "ISO",
                "current": "100", "choices": ["100", "200"],
            }],
            "video_settings": {"resolution": ["640x480"]},
        }

    def list_files(self):
        return list(self.files)

    def start_liveview(self, video_settings):
        self.start_calls += 1
        return {"success": True, "video_settings": video_settings}

    def iter_liveview_frames(self, stop_event, on_frame):
        on_frame(b"\xff\xd8fake-jpeg\xff\xd9")
        stop_event.wait(2.0)

    def close_liveview_stream(self):
        self.stream_closed.set()

    def stop_liveview(self):
        self.stop_calls += 1
        return {"success": True}

    def save_liveview_snapshot(self, file_name, crop):
        remote = RemoteFile("snapshot-2", f"{file_name or 'snapshot'}.jpg", "snapshot", 16)
        self.files.append(remote)
        return remote

    def capture_photo(self, photo_settings, file_name, crop):
        self.last_photo_settings = photo_settings
        self.last_crop = crop
        remote = RemoteFile("photo-2", f"{file_name or 'photo'}.jpg", "photo", 16)
        self.files.append(remote)
        return remote

    def download_file(self, remote, output_dir, preferred_name=""):
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (preferred_name or remote.name)
        target.write_bytes(b"verified-content")
        return target

    def delete_file(self, remote):
        file_id = remote.file_id if isinstance(remote, RemoteFile) else str(remote)
        self.files = [item for item in self.files if item.file_id != file_id]
        return {"success": True}


class BrokenCameraClient(FakeCameraClient):
    def health(self):
        raise OSError("service offline")


class V2CameraControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeCameraClient.instances.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.controller = CameraController(
            client_factory=FakeCameraClient,
            default_download_dir=self.temp_dir.name,
        )

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_connect_initializes_and_lists_remote_files(self) -> None:
        state = self.controller.connect("camera.local", 8765)

        self.assertTrue(state["connected"])
        self.assertTrue(state["initialized"])
        self.assertEqual(state["base_url"], "http://camera.local:8765")
        self.assertEqual(state["camera_status"]["model"], "Fake Canon")
        self.assertEqual(state["files"][0]["name"], "preview.jpg")
        self.assertEqual(state["preferences"]["photo_settings"]["/main/imgsettings/iso"], "100")
        self.assertEqual(FakeCameraClient.instances[-1].initialize_calls, 1)

    def test_liveview_exposes_latest_frame_and_stops_stream(self) -> None:
        self.controller.connect("192.168.4.1", 8765)
        started = self.controller.start_liveview()
        deadline = time.monotonic() + 2.0
        while self.controller.snapshot()["frame_sequence"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        state = self.controller.snapshot()
        self.assertTrue(started["liveview_active"])
        self.assertEqual(state["frame_sequence"], 1)
        self.assertEqual(self.controller.frame(), b"\xff\xd8fake-jpeg\xff\xd9")

        stopped = self.controller.stop_liveview()
        client = FakeCameraClient.instances[-1]
        self.assertFalse(stopped["liveview_active"])
        self.assertTrue(client.stream_closed.is_set())
        self.assertEqual(client.stop_calls, 1)

    def test_disconnect_clears_camera_session(self) -> None:
        self.controller.connect("camera.local", 9000)

        state = self.controller.disconnect()

        self.assertFalse(state["connected"])
        self.assertEqual(state["files"], [])
        with self.assertRaisesRegex(RuntimeError, "Сначала подключитесь"):
            self.controller.refresh()

    def test_connection_failure_is_visible_in_snapshot(self) -> None:
        controller = CameraController(client_factory=BrokenCameraClient)
        with self.assertRaisesRegex(OSError, "service offline"):
            controller.connect("broken.local", 8765)

        state = controller.snapshot()
        self.assertFalse(state["connected"])
        self.assertIn("service offline", state["error"])

    def test_port_is_validated_before_network_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "от 1 до 65535"):
            self.controller.connect("camera.local", 70000)

    def test_preferences_validate_dynamic_controls_and_crop(self) -> None:
        self.controller.connect("camera.local", 8765)

        state = self.controller.update_preferences(
            {"/main/imgsettings/imageformat": "Medium JPEG", "/main/imgsettings/iso": "200"},
            {"width_percent": 75, "height_percent": 60},
            False,
        )

        self.assertEqual(state["preferences"]["crop"], {"width_percent": 75.0, "height_percent": 60.0})
        self.assertFalse(state["preferences"]["keep_remote_files"])
        with self.assertRaisesRegex(ValueError, "Недопустимое значение"):
            self.controller.update_preferences(
                {"/main/imgsettings/iso": "64000"},
                {"width_percent": 100, "height_percent": 100},
            )

    def test_snapshot_is_verified_and_remote_copy_can_be_deleted(self) -> None:
        self.controller.connect("camera.local", 8765)
        self.controller.start_liveview()

        state = self.controller.capture(
            "snapshot", "sample", {},
            {"width_percent": 80, "height_percent": 70}, False,
        )

        transfer = state["last_transfer"]
        self.assertTrue(Path(transfer["local_file"]).is_file())
        self.assertTrue(transfer["remote_deleted"])
        self.assertNotIn("snapshot-2", [item["file_id"] for item in state["files"]])

    def test_full_photo_restarts_liveview_and_applies_settings(self) -> None:
        self.controller.connect("camera.local", 8765)
        self.controller.start_liveview()
        client = FakeCameraClient.instances[-1]

        state = self.controller.capture(
            "photo", "full", {"/main/imgsettings/iso": "200"},
            {"width_percent": 90, "height_percent": 90}, True,
        )

        self.assertTrue(state["liveview_active"])
        self.assertEqual(client.start_calls, 2)
        self.assertEqual(client.stop_calls, 1)
        self.assertEqual(client.last_photo_settings, {"/main/imgsettings/iso": "200"})
        self.assertEqual(client.last_crop, {"width_percent": 90.0, "height_percent": 90.0})

    def test_existing_remote_file_can_be_downloaded_then_deleted(self) -> None:
        self.controller.connect("camera.local", 8765)

        downloaded = self.controller.download("file-1")
        self.assertTrue(Path(downloaded["last_transfer"]["local_file"]).is_file())

        deleted = self.controller.delete("file-1")
        self.assertEqual(deleted["files"], [])


if __name__ == "__main__":
    unittest.main()
