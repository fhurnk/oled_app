from __future__ import annotations

import threading
import time
import unittest

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
        self.__class__.instances.append(self)

    def health(self):
        return {"status": "ok", "service": "oled-camera"}

    def initialize(self):
        self.initialize_calls += 1
        return {"success": True}

    def status(self):
        return {"model": "Fake Canon", "liveview_active": self.start_calls > self.stop_calls}

    def capabilities(self):
        return {"video_settings": {"resolution": ["640x480"]}}

    def list_files(self):
        return [RemoteFile("file-1", "preview.jpg", "photo", 321, "2026-09-28T10:00:00Z", "abc")]

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


class BrokenCameraClient(FakeCameraClient):
    def health(self):
        raise OSError("service offline")


class V2CameraControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeCameraClient.instances.clear()
        self.controller = CameraController(client_factory=FakeCameraClient)

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_connect_initializes_and_lists_remote_files(self) -> None:
        state = self.controller.connect("camera.local", 8765)

        self.assertTrue(state["connected"])
        self.assertTrue(state["initialized"])
        self.assertEqual(state["base_url"], "http://camera.local:8765")
        self.assertEqual(state["camera_status"]["model"], "Fake Canon")
        self.assertEqual(state["files"][0]["name"], "preview.jpg")
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


if __name__ == "__main__":
    unittest.main()
