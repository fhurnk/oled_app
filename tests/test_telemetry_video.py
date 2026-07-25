from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from oled_app.camera.telemetry_video import (
    StabilityTelemetrySample,
    _probe_video,
    build_telemetry_intervals,
    create_stability_telemetry_video,
    find_ffmpeg_executable,
    read_stability_telemetry,
)


def write_stability_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append(["OLED stability"])
    worksheet.append([])
    worksheet.append(
        [
            "Time (s)",
            "Voltage setpoint (V)",
            "Applied voltage (V)",
            "Voltage OLED / LED (V)",
            "Current OLED / LED (mA)",
        ]
    )
    worksheet.append([0.0, 4.0, 4.0, 3.999, 1.2])
    worksheet.append([1.0, None, 3.2, 3.198, 1.3])
    workbook.save(path)
    workbook.close()


class StabilityTelemetryVideoTests(unittest.TestCase):
    def test_reads_voltage_setpoint_instead_of_measured_voltage(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workbook_path = Path(folder) / "stability.xlsx"
            write_stability_workbook(workbook_path)

            samples = read_stability_telemetry(workbook_path)

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0], StabilityTelemetrySample(0.0, 4.0, 1.2))
        self.assertEqual(samples[1], StabilityTelemetrySample(1.0, 3.2, 1.3))

    def test_maps_measurement_points_to_video_with_preroll_and_last_value_hold(self) -> None:
        samples = [
            StabilityTelemetrySample(0.0, 3.1, 1.2),
            StabilityTelemetrySample(1.0, 3.2, 1.3),
        ]

        intervals = build_telemetry_intervals(
            samples,
            measurement_time_at_video_start_s=-0.5,
            video_duration_s=2.5,
        )

        self.assertEqual(intervals[0], (0.0, 0.5, None))
        self.assertEqual(intervals[1], (0.5, 1.5, samples[0]))
        self.assertEqual(intervals[2], (1.5, 2.5, samples[1]))

    def test_creates_expanded_copy_and_preserves_source_video(self) -> None:
        try:
            ffmpeg = find_ffmpeg_executable()
        except RuntimeError as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory(prefix="oled telemetry test ") as folder:
            root = Path(folder)
            source = root / "source video.mp4"
            subprocess.run(
                [
                    str(ffmpeg),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:d=1.5:r=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
                check=True,
            )
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            workbook_path = root / "stability.xlsx"
            write_stability_workbook(workbook_path)
            source.with_name(f"{source.stem}_sync.json").write_text(
                json.dumps(
                    {
                        "measurement_sync": True,
                        "measurement_time_at_video_start_s": -0.2,
                        "pixel_id": "CR1_1_1",
                        "events": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = create_stability_telemetry_video(source, workbook_path)
            width, height, duration_s = _probe_video(ffmpeg, output)

            self.assertEqual(output.name, "source video_telemetry.mp4")
            self.assertEqual((width, height), (320, 260))
            self.assertGreater(duration_s, 1.0)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)


if __name__ == "__main__":
    unittest.main()
