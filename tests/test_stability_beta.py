from __future__ import annotations

import unittest
import csv
import tempfile
from pathlib import Path
from types import SimpleNamespace

from oled_app.measurements.stability import (
    StabilityParams,
    StabilitySetpointController,
    next_stability_voltage,
    run_stability_measurement,
)
from oled_app.hardware import uninstall_simulator_modules
from oled_app.gui.camera_window import CameraTestWindow
from oled_app.processing.stability_results import raw_row_to_workbook_row


class StabilitySetpointTests(unittest.TestCase):
    def test_controller_updates_clamps_and_stops(self) -> None:
        controller = StabilitySetpointController("voltage", 3.0, maximum=5.0)

        self.assertEqual(controller.add(0.25), 3.25)
        self.assertEqual(controller.add(10.0), 5.0)
        self.assertEqual(controller.add(-0.5), 4.5)
        self.assertEqual(controller.set_target(2.5), 2.5)
        controller.request_stop()

        target, revision, stopped = controller.snapshot()
        self.assertEqual(target, 2.5)
        self.assertGreaterEqual(revision, 3)
        self.assertTrue(stopped)

    def test_voltage_mode_applies_target_immediately(self) -> None:
        params = StabilityParams(control_mode="voltage", voltage_step_max=0.25, voltage_limit=5.0)

        next_voltage, limit_reached = next_stability_voltage("voltage", 3.0, 4.0, 2.0, params)

        self.assertEqual(next_voltage, 4.0)
        self.assertFalse(limit_reached)

    def test_current_mode_keeps_software_feedback(self) -> None:
        params = StabilityParams(
            control_mode="current",
            voltage_step_max=0.2,
            current_control_kp=0.1,
            voltage_limit=5.0,
        )

        next_voltage, limit_reached = next_stability_voltage("current", 3.0, 5.0, 2.0, params)

        self.assertEqual(next_voltage, 3.2)
        self.assertFalse(limit_reached)

    def test_voltage_target_and_applied_voltage_are_preserved_in_workbook_row(self) -> None:
        row = raw_row_to_workbook_row(
            {
                "point": "1",
                "date_time": "2026-07-17 12:00:00",
                "elapsed_s": "2.5",
                "control_mode": "voltage",
                "voltage_setpoint_V": "4.0",
                "voltage_set_V": "3.25",
                "voltage_led_measured_V": "3.24",
                "current_led_mA": "2.0",
                "current_photodiode_uA": "1.0",
            },
            StabilityParams(control_mode="voltage"),
        )

        self.assertEqual(row[3], "voltage")
        self.assertEqual(row[5], 4.0)
        self.assertEqual(row[6], 3.25)

    def test_voltage_mode_runs_with_dynamic_target_in_simulator(self) -> None:
        controller = StabilitySetpointController("voltage", 1.0, maximum=5.0)
        params = StabilityParams(
            control_mode="voltage",
            voltage_setpoint_V=1.0,
            voltage_start=1.0,
            voltage_step_max=0.25,
            voltage_limit=5.0,
            current_limit_mA=20.0,
            measurement_time_s=0.08,
            sample_interval_s=0.01,
            autosave_interval_s=60.0,
        )
        changed = False

        def progress(_point):
            nonlocal changed
            if not changed:
                controller.add(0.5)
                changed = True

        try:
            with tempfile.TemporaryDirectory() as folder:
                result = run_stability_measurement(
                    "Q1_1_1",
                    Path(folder),
                    params,
                    lambda _message: None,
                    {
                        "hardware_mode": "simulator",
                        "raw_data": {"policy": "keep_separate", "folder_name": "raw_data"},
                    },
                    control=controller,
                    progress=progress,
                )
                self.assertTrue(result["file"].exists())
                self.assertEqual(result["final_setpoint"], 1.5)
                self.assertEqual(result["control_mode"], "voltage")
        finally:
            uninstall_simulator_modules()


class VideoMeasurementTimelineTests(unittest.TestCase):
    def test_each_video_second_maps_to_measurement_elapsed_time(self) -> None:
        class Value:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        session = {
            "measurement_type": "IVL",
            "pixel_id": "CR1_2_3",
            "started_monotonic": 100.0,
            "started_at": "2026-07-17 12:00:00",
            "events": [
                {
                    "event": "current_limit_or_breakdown",
                    "label": "Лимит тока / возможный пробой или шунт",
                    "measurement_time_s": 6.5,
                }
            ],
        }
        app = SimpleNamespace(
            series=object(),
            measurement_session_for_interval=lambda *_args: session,
        )
        camera = SimpleNamespace(
            series_bound=True,
            app=app,
            _recording_started_monotonic=105.0,
            _recording_stop_requested_monotonic=107.2,
            _recording_context={},
            pixel_var=Value("CR1_2_3"),
            station_var=Value("ВАЯХ"),
            _log=lambda _message: None,
        )

        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "CR1_2_3_ivl_video.mp4"
            video.touch()
            metadata = CameraTestWindow._write_video_measurement_timeline(camera, video)
            with (Path(folder) / "CR1_2_3_ivl_video_timeline.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))

        self.assertTrue(metadata["measurement_sync"])
        self.assertEqual(float(rows[0]["measurement_time_s"]), 5.0)
        self.assertEqual(float(rows[1]["measurement_time_s"]), 6.0)
        self.assertEqual(float(rows[-1]["measurement_time_s"]), 7.2)
        self.assertEqual(metadata["events"][0]["video_time_s"], 1.5)
        event_row = next(row for row in rows if row["event"])
        self.assertEqual(float(event_row["video_time_s"]), 1.5)


if __name__ == "__main__":
    unittest.main()
