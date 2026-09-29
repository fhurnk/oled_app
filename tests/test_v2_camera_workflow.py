from __future__ import annotations

from copy import deepcopy
import tempfile
import threading
import time
import unittest
from pathlib import Path
import json
import urllib.error
import urllib.request

from oled_v2.config import CLIENT_HEADER, SESSION_HEADER
from oled_v2.camera_workflow import GuidedCameraWorkflow, stability_postroll_remaining_s
from oled_v2.server import LocalBackend


class FakeCamera:
    def __init__(self, root: Path, station: str = "stability") -> None:
        self.root = root
        self.root.mkdir(parents=True)
        self.station = station
        self.recording = False
        self.captures = []
        self.derivatives = []
        self.last_transfer = None

    def snapshot(self):
        return {
            "connected": True,
            "initialized": True,
            "recording_active": self.recording,
            "mode": "series",
            "series_target": {
                "series_path": str(self.root.parent),
                "series_name": "guided-series",
                "pixel_id": "Q1_1_1",
                "station": self.station,
                "station_label": "Стабильность" if self.station == "stability" else "ВАЯХ",
                "session_dir": str(self.root),
            },
            "last_transfer": deepcopy(self.last_transfer),
        }

    def capture(self, kind, file_name):
        path = self.root / f"{file_name}.jpg"
        path.write_bytes(b"jpeg")
        self.captures.append((kind, file_name))
        self.last_transfer = {"local_file": str(path)}
        return self.snapshot()

    def start_recording(self):
        self.recording = True
        return self.snapshot()

    def stop_recording(self):
        self.recording = False
        path = self.root / "guided.mp4"
        path.write_bytes(b"video")
        self.last_transfer = {"local_file": str(path)}
        return self.snapshot()

    def record_series_derivative(self, path, kind, source_name, extra_params=None):
        self.derivatives.append((Path(path), kind, source_name, extra_params))
        return Path(path)


class FakeMeasurement:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.state = {"active": False, "status": "idle", "run_id": None, "result": None}
        self.preflight_payload = None

    def preflight(self, payload):
        self.preflight_payload = deepcopy(payload)
        return {"target": deepcopy(payload.get("target"))}

    def start(self, payload):
        with self._lock:
            self.state = {
                "active": True,
                "status": "running",
                "run_id": "guided-run",
                "result": None,
            }
            return self.snapshot()

    def complete(self, result):
        with self._lock:
            self.state.update(active=False, status="completed", result=deepcopy(result))

    def snapshot(self):
        with self._lock:
            return deepcopy(self.state)

    def stop(self):
        with self._lock:
            if self.state["active"]:
                self.state.update(active=False, status="stopped")
            return self.snapshot()


def wait_status(workflow: GuidedCameraWorkflow, expected: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while workflow.snapshot()["status"] != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    state = workflow.snapshot()
    if state["status"] != expected:
        raise AssertionError(f"Expected {expected}, got {state}")
    return state


class GuidedCameraWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.camera = FakeCamera(self.root / "04_CAMERA" / "1")
        self.ivl = FakeMeasurement()
        self.stability = FakeMeasurement()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stability_workflow_preserves_video_and_builds_telemetry_copy(self) -> None:
        def telemetry_builder(video: Path, workbook: Path) -> Path:
            self.assertTrue(video.is_file())
            self.assertTrue(workbook.is_file())
            output = video.with_name(video.stem + "_telemetry.mp4")
            output.write_bytes(b"telemetry")
            return output

        workflow = GuidedCameraWorkflow(
            self.camera,
            self.ivl,
            self.stability,
            telemetry_builder=telemetry_builder,
            postroll_s=0.02,
        )
        target = {"series_path": str(self.camera.root.parent), "pixel_id": "Q1_1_1"}
        measurement = {"target": target, "measurement_time_s": 1.0}
        prepared = workflow.prepare("stability", measurement, create_telemetry=True)
        self.assertEqual(prepared["status"], "awaiting_measurement")
        self.assertTrue(Path(prepared["before_photo"]).is_file())
        started = workflow.continue_measurement()
        self.assertEqual(started["status"], "measuring")
        self.assertTrue(self.camera.recording)

        workbook = self.root / "stability.xlsx"
        workbook.write_bytes(b"workbook")
        self.stability.complete({
            "file": str(workbook),
            "status": "CURRENT_LIMIT",
            "events": [{"event": "current_limit_or_breakdown", "measurement_time_s": 0.0}],
        })
        ready = wait_status(workflow, "awaiting_after_photo")
        self.assertFalse(self.camera.recording)
        self.assertTrue(Path(ready["video_file"]).is_file())
        self.assertTrue(Path(ready["sync_file"]).is_file())
        self.assertTrue(Path(ready["timeline_file"]).is_file())
        self.assertTrue(Path(ready["telemetry_file"]).is_file())
        self.assertEqual(
            {item[1] for item in self.camera.derivatives},
            {"video_sync", "video_timeline", "video_telemetry"},
        )

        completed = workflow.finish(take_photo=True)
        self.assertEqual(completed["status"], "completed")
        self.assertFalse(completed["active"])
        self.assertTrue(Path(completed["after_photo"]).is_file())
        self.assertEqual(
            [name for _kind, name in self.camera.captures],
            ["photo_before", "photo_after"],
        )

    def test_target_must_match_camera_binding(self) -> None:
        workflow = GuidedCameraWorkflow(self.camera, self.ivl, self.stability)
        with self.assertRaisesRegex(RuntimeError, "не совпадает"):
            workflow.prepare("stability", {
                "target": {"series_path": str(self.camera.root.parent), "pixel_id": "Q1_1_2"}
            })
        self.assertEqual(workflow.snapshot()["status"], "idle")

    def test_postroll_uses_measurement_event_time(self) -> None:
        result = {"events": [{
            "event": "current_limit_or_breakdown",
            "measurement_time_s": 4.0,
        }]}
        self.assertAlmostEqual(
            stability_postroll_remaining_s(result, 100.0, now_monotonic=107.5),
            1.5,
        )
        self.assertEqual(
            stability_postroll_remaining_s(result, 100.0, now_monotonic=110.0),
            0.0,
        )

    def test_guided_api_recovers_state_and_blocks_direct_measurement_start(self) -> None:
        with LocalBackend(series_root=self.root / "series") as backend:
            app = backend.server.config.app
            guided = app.state.guided_camera
            camera = FakeCamera(self.root / "api-camera", station="ivl")
            measurement = FakeMeasurement()
            guided.camera = camera
            guided.controllers["ivl"] = measurement
            headers = {
                SESSION_HEADER: backend.session.token,
                CLIENT_HEADER: "guided-camera-test-0001",
                "Content-Type": "application/json",
            }

            def request(path, payload=None):
                req = urllib.request.Request(
                    backend.session.origin + path,
                    headers=headers,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    method="POST" if payload is not None else "GET",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as exc:
                    return exc.code, json.load(exc)

            target = {"series_path": str(camera.root.parent), "pixel_id": "Q1_1_1"}
            code, prepared = request("/api/camera/guided/prepare", {
                "station": "ivl", "measurement": {"target": target},
                "create_telemetry": False,
            })
            self.assertEqual(code, 200, prepared)
            self.assertEqual(prepared["status"], "awaiting_measurement")
            self.assertEqual(request("/api/ivl/start", {})[0], 409)
            self.assertEqual(request("/api/camera/guided/state")[1]["workflow_id"], prepared["workflow_id"])

            code, running = request("/api/camera/guided/continue", {})
            self.assertEqual(code, 200, running)
            measurement.complete({"status": "WORKING", "file": "ivl.xlsx", "events": []})
            wait_status(guided, "awaiting_after_photo")
            code, finished = request("/api/camera/guided/finish", {"take_photo": False})
            self.assertEqual(code, 200, finished)
            self.assertEqual(finished["status"], "completed")


if __name__ == "__main__":
    unittest.main()
