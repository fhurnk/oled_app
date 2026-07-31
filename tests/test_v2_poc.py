from __future__ import annotations

import json
import logging
import tempfile
import time
import unittest
import urllib.request
from copy import deepcopy
from pathlib import Path

from websockets.sync.client import connect

from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_v2.config import CLIENT_HEADER, SESSION_HEADER
from oled_v2.poc import PocBusyError, PocController
from oled_v2.server import LocalBackend


CLIENT_ID = "stage2-poc-test-client-0001"


def wait_for_terminal(controller: PocController, timeout_s: float = 4.0) -> dict:
    deadline = time.monotonic() + timeout_s
    state = controller.snapshot()
    while state["active"] and time.monotonic() < deadline:
        time.sleep(0.01)
        state = controller.snapshot()
    return state


class PocControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.logger = logging.getLogger(f"oled-v2-poc-test-{id(self)}")
        self.logger.addHandler(logging.NullHandler())

        def settings_provider() -> dict:
            settings = deepcopy(DEFAULT_APP_SETTINGS)
            settings["simulator_config_path"] = str(
                Path(self.temp_dir.name) / "simulator.json"
            )
            return settings

        self.controller = PocController(
            settings_provider=settings_provider,
            logger=self.logger,
        )

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_simulator_poc_streams_smu_and_spectrometer_points(self) -> None:
        self.controller.start_simulator(point_count=8, interval_s=0.005)
        state = wait_for_terminal(self.controller)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["point_count"], 8)
        self.assertTrue(state["safe_shutdown_confirmed"])
        self.assertEqual(state["spectrometer_model"], "OLED-SIM-SPECTROMETER")
        self.assertGreater(state["latest_point"]["spectrum_peak_counts"], 0)
        self.assertGreater(state["latest_point"]["photodiode_uA"], 0)

    def test_operator_stop_confirms_safe_smu_shutdown(self) -> None:
        self.controller.start_simulator(point_count=120, interval_s=0.05)
        deadline = time.monotonic() + 2.0
        while self.controller.snapshot()["point_count"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        state = self.controller.stop_and_wait(timeout_s=2.0)

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["stop_reason"], "operator")
        self.assertTrue(state["safe_shutdown_confirmed"])

    def test_second_operation_is_rejected_while_active(self) -> None:
        self.controller.start_simulator(point_count=120, interval_s=0.05)

        with self.assertRaises(PocBusyError):
            self.controller.start_simulator(point_count=8, interval_s=0.005)

    def test_hardware_probe_is_rejected_while_operation_is_active(self) -> None:
        self.controller.start_simulator(point_count=120, interval_s=0.05)

        with self.assertRaises(PocBusyError):
            self.controller.probe_current_hardware()


class PocWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        static_root = Path(self.temp_dir.name)
        (static_root / "assets").mkdir()
        (static_root / "index.html").write_text(
            "<!doctype html><html><body><div id='root'>Stage 2</div></body></html>",
            encoding="utf-8",
        )
        self.backend = LocalBackend(static_root=static_root)
        self.session = self.backend.start()
        self.headers = {
            SESSION_HEADER: self.session.token,
            CLIENT_HEADER: CLIENT_ID,
            "Content-Type": "application/json",
        }

    def tearDown(self) -> None:
        self.backend.stop()
        self.temp_dir.cleanup()

    def post(self, path: str, payload: dict):
        return urllib.request.urlopen(
            urllib.request.Request(
                f"{self.session.origin}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers=self.headers,
                method="POST",
            ),
            timeout=4.0,
        )

    def test_authenticated_websocket_receives_live_points_and_terminal_state(self) -> None:
        ws_url = self.session.origin.replace("http://", "ws://") + "/api/poc/stream"
        protocols = [
            "oled-v2",
            f"oled-session.{self.session.token}",
            f"oled-client.{CLIENT_ID}",
        ]
        received_points = []
        terminal_state = None

        with connect(
            ws_url,
            origin=self.session.origin,
            subprotocols=protocols,
            proxy=None,
            open_timeout=3.0,
            close_timeout=1.0,
        ) as websocket:
            snapshot = json.loads(websocket.recv(timeout=3.0))
            self.assertEqual(snapshot["type"], "poc_snapshot")
            self.assertEqual(websocket.subprotocol, "oled-v2")

            with self.post(
                "/api/poc/start",
                {"point_count": 8, "interval_ms": 5},
            ) as response:
                self.assertEqual(response.status, 202)

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                event = json.loads(websocket.recv(timeout=2.0))
                if event["type"] == "poc_point":
                    received_points.append(event["point"])
                elif event["type"] == "poc_state":
                    state = event["state"]
                    if state["status"] in {"completed", "stopped", "safety_limit", "failed"}:
                        terminal_state = state
                        break

        self.assertEqual(len(received_points), 8)
        self.assertIsNotNone(terminal_state)
        self.assertEqual(terminal_state["status"], "completed")
        self.assertTrue(terminal_state["safe_shutdown_confirmed"])


if __name__ == "__main__":
    unittest.main()
