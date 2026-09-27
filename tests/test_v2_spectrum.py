import tempfile
import time
import unittest
from pathlib import Path
import json
import urllib.error
import urllib.request

from openpyxl import load_workbook

from oled_v2.series_service import SeriesService, SeriesValidationError
from oled_v2.server import LocalBackend
from oled_v2.config import CLIENT_HEADER, SESSION_HEADER
from oled_v2.spectrum import SpectrumController


def wait_terminal(controller, timeout=15):
    deadline = time.monotonic() + timeout
    while controller.snapshot()["active"] and time.monotonic() < deadline:
        time.sleep(0.01)
    state = controller.snapshot()
    if state["active"]:
        raise AssertionError("Spectrum did not terminate")
    return state


FAST_PARAMS = {
    "voltage_start": 3.0,
    "voltage_end": 3.1,
    "voltage_step": 0.1,
    "settle_time_voltage_s": 0.0,
    "settle_time_spectrum_s": 0.0,
    "discard_first_scan_after_tint_change": False,
}


class SpectrumControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.controller = SpectrumController(self.root / "standalone")

    def tearDown(self):
        self.controller.shutdown()
        self.temp.cleanup()

    def test_invalid_preflight_is_read_only(self):
        for payload in (
            {"voltage_step": 0}, {"voltage_end": 1.0, "voltage_start": 2.0},
            {"target_intensity": 1000, "intensity_min": 2000},
            {"t_int_initial_s": 2, "t_int_max_s": 1},
            {"max_iterations": 1.5}, {"led_type": "ultraviolet"},
            {"hardware_mode": "real"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.controller.preflight(payload)
        self.assertFalse(self.controller.output_root.exists())

    def test_standalone_builds_compatible_workbook_and_preview(self):
        self.controller.start(FAST_PARAMS)
        state = wait_terminal(self.controller)
        self.assertEqual(state["status"], "completed", state)
        self.assertTrue(state["safe_shutdown_confirmed"])
        self.assertEqual(state["point_count"], 2)
        self.assertIsNotNone(state["latest_spectrum"])
        self.assertGreater(len(state["latest_spectrum"]["wavelengths_nm"]), 100)
        workbook = load_workbook(state["result"]["file"], read_only=True)
        try:
            self.assertIn("Сводка", workbook.sheetnames)
            self.assertGreaterEqual(len(workbook.sheetnames), 2)
        finally:
            workbook.close()
        state["points"].clear()
        self.assertEqual(len(self.controller.snapshot()["points"]), 2)

    def test_selected_series_pixel_is_journaled(self):
        service = SeriesService(self.root / "series")
        active = service.create_series({
            "root": str(self.root / "series"), "deposition_date": "2026-09-27",
            "keyword": "spectrum", "series_led_color": "green",
            "quarter_bases": {str(number): "Q" for number in range(1, 5)},
            "quarter_descriptions": {str(number): "Test" for number in range(1, 5)},
        })["active"]
        self.controller.series_service = service
        pixel_id = active["pixels"][0]["pixel_id"]
        self.controller.start({
            **FAST_PARAMS,
            "target": {"series_path": active["path"], "pixel_id": pixel_id},
            "use_opening_voltage": False,
        })
        state = wait_terminal(self.controller)
        self.assertEqual(state["status"], "completed", state)
        self.assertTrue(state["result"]["journaled"])
        reopened = service.open_series(active["path"])["active"]
        pixel = next(item for item in reopened["pixels"] if item["pixel_id"] == pixel_id)
        self.assertTrue(pixel["last_spectrum_file"])
        self.assertEqual(reopened["metrics"]["spectra"], 1)
        self.assertEqual(reopened["history"][-1]["type"], "SPECTRUM")
        self.assertIn("ЭМУЛЯТОР v2", reopened["history"][-1]["notes"])

    def test_opening_voltage_is_required_only_when_requested(self):
        service = SeriesService(self.root / "series-opening")
        active = service.create_series({
            "root": str(self.root / "series-opening"), "deposition_date": "2026-09-27",
            "keyword": "opening", "series_led_color": "blue",
            "quarter_bases": {str(number): "B" for number in range(1, 5)},
            "quarter_descriptions": {str(number): "Test" for number in range(1, 5)},
        })["active"]
        self.controller.series_service = service
        target = {"series_path": active["path"], "pixel_id": active["pixels"][0]["pixel_id"]}
        with self.assertRaisesRegex(SeriesValidationError, "нет напряжения открытия"):
            self.controller.preflight({**FAST_PARAMS, "target": target, "use_opening_voltage": True})
        checked = self.controller.preflight({**FAST_PARAMS, "target": target, "use_opening_voltage": False})
        self.assertEqual(checked["effective_voltage_start"], 3.0)

    def test_api_auth_busy_gate_and_snapshot_recovery(self):
        with LocalBackend(series_root=self.root / "api-series") as backend:
            controller = backend.server.config.app.state.spectrum_controller
            controller.output_root = self.root / "api-output"
            headers = {}

            def request(path, payload=None, method=None):
                req = urllib.request.Request(
                    backend.session.origin + path,
                    headers=headers,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    method=method,
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as exc:
                    return exc.code, json.load(exc)

            self.assertEqual(request("/api/spectrum/state")[0], 401)
            headers.update({
                SESSION_HEADER: backend.session.token,
                CLIENT_HEADER: "spectrum-test-client-0001",
                "Content-Type": "application/json",
            })
            self.assertEqual(request("/api/spectrum/preflight", {"voltage_step": 0})[0], 422)
            code, started = request("/api/spectrum/start", {
                "settle_time_voltage_s": 0.5,
                "settle_time_spectrum_s": 0.0,
                "discard_first_scan_after_tint_change": False,
            })
            self.assertEqual(code, 202)
            self.assertEqual(request("/api/ivl/start", {})[0], 409)
            self.assertEqual(request("/api/poc/start", {})[0], 409)
            self.assertEqual(request("/api/series/close", {}, "POST")[0], 409)
            snapshot = request("/api/spectrum/state")[1]
            self.assertEqual(snapshot["run_id"], started["run_id"])
            request("/api/spectrum/stop", {})
            state = wait_terminal(controller)
            self.assertEqual(state["status"], "stopped")
            self.assertTrue(state["safe_shutdown_confirmed"])


if __name__ == "__main__":
    unittest.main()
