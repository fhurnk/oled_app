from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from oled_app.hardware.probe import probe_spectrometer
from oled_app.gui.spectrum_window import initial_spectrum_start_value
from oled_app.measurements.spectrum import (
    SpectrumHelper,
    SpectrumMeasurementController,
    SpectrumMeasurementStopped,
    SpectrumParams,
    SpectrumPixelRejected,
    save_rejected_spectrum_workbook,
    spectrum_electrical_status,
)
from oled_app.measurements.raw_io import RawCsvWriter
from oled_app.processing.spectrum_results import (
    SPECTRUM_SPECTRA_RAW_HEADERS,
    SPECTRUM_SUMMARY_RAW_HEADERS,
)


class SpectrometerProbeTests(unittest.TestCase):
    @patch("oled_app.hardware.probe.subprocess.run")
    def test_native_probe_timeout_is_reported_without_raising(self, run_mock):
        run_mock.side_effect = subprocess.TimeoutExpired(["python", "-c"], 0.1)

        self.assertEqual(probe_spectrometer(0.1), ("timeout", 0))

    @patch("oled_app.hardware.probe.subprocess.run")
    def test_native_probe_count_is_parsed_from_child_marker(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["python", "-c"],
            0,
            stdout="driver message\nOLED_SPEC_COUNT=1\n",
            stderr="",
        )

        self.assertEqual(probe_spectrometer(), ("ok", 1))


class SpectrumQualityTests(unittest.TestCase):
    def test_kept_rejected_data_builds_diagnostic_xlsx(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            summary = root / "summary.csv"
            spectra = root / "spectra.csv"
            output = root / "diagnostic.xlsx"
            with RawCsvWriter(summary, SPECTRUM_SUMMARY_RAW_HEADERS) as writer:
                writer.writerow(
                    {
                        "point": 1,
                        "date_time": "2026-07-30 12:00:00",
                        "voltage_set_V": 3.0,
                        "voltage_led_measured_V": 3.0,
                        "current_led_A": 0.001,
                        "current_led_mA": 1.0,
                        "current_density_mA_cm2": 100.0,
                        "voltage_photodiode_measured_V": -5.0,
                        "current_photodiode_A": -0.000001,
                        "current_photodiode_uA": 1.0,
                        "luminance_cd_m2": 1.0,
                        "integration_time_s": 0.01,
                        "status": "CURRENT_LIMIT",
                    }
                )
            with RawCsvWriter(spectra, SPECTRUM_SPECTRA_RAW_HEADERS) as writer:
                for wavelength, counts in ((500.0, 100.0), (510.0, 120.0)):
                    writer.writerow(
                        {
                            "point": 1,
                            "voltage_set_V": 3.0,
                            "integration_time_s": 0.01,
                            "wavelength_nm": wavelength,
                            "raw_counts": counts,
                        }
                    )
            result = {
                "file": None,
                "pending_file": output,
                "raw_files": [summary, spectra],
            }

            diagnostic = save_rejected_spectrum_workbook(
                "P1",
                SpectrumParams(voltage_start=3.0, voltage_end=3.0),
                result,
            )

            self.assertEqual(diagnostic, output)
            self.assertTrue(output.exists())
            self.assertIsNone(result["file"])

    def test_last_manual_start_voltage_is_kept_in_next_window(self):
        self.assertEqual(initial_spectrum_start_value({"voltage_start_V": "2.75"}, 2.1), "2.75")

    def test_status_uses_raw_counts_before_baseline_subtraction(self):
        params = SpectrumParams(led_type="visible")
        helper = SpectrumHelper(params, lambda _message: None)
        wavelengths = np.linspace(380.0, 780.0, 401)
        raw = 15000.0 + 15000.0 * np.exp(-0.5 * ((wavelengths - 620.0) / 55.0) ** 2)
        processed = helper.process_spectrum(wavelengths, raw, None, 0.1)

        *_, acquisition_status = helper.analyze_raw_quality(wavelengths, processed["raw"])
        *_, processed_status = helper.analyze_quality(wavelengths, processed["baseline_corrected"], "visible")

        self.assertIn(acquisition_status, {"GOOD", "OK"})
        self.assertEqual(processed_status, "TOO_WEAK")

    def test_processed_peak_metrics_keep_intensity_and_wavelength_in_correct_order(self):
        helper = SpectrumHelper(SpectrumParams(led_type="visible"), lambda _message: None)
        wavelengths = np.linspace(380.0, 780.0, 401)
        processed = 15000.0 * np.exp(-0.5 * ((wavelengths - 620.0) / 30.0) ** 2)

        peak_int, peak_wl, _ = helper.processed_peak_metrics(wavelengths, processed)

        self.assertAlmostEqual(peak_int, 15000.0)
        self.assertAlmostEqual(peak_wl, 620.0)

    def test_previous_exposure_is_reused_above_and_below_ten_ms(self):
        helper = SpectrumHelper(SpectrumParams(), lambda _message: None)

        self.assertEqual(helper.next_initial_integration_time(0.100, 30000.0), 0.100)
        self.assertEqual(helper.next_initial_integration_time(0.005, 30000.0), 0.005)

    def test_intense_previous_spectrum_reduces_next_exposure(self):
        helper = SpectrumHelper(SpectrumParams(target_intensity=40000.0), lambda _message: None)

        self.assertAlmostEqual(helper.next_initial_integration_time(0.100, 80000.0), 0.050)
        self.assertAlmostEqual(helper.next_initial_integration_time(0.005, 50000.0), 0.004)

    def test_wide_peak_is_not_rejected(self):
        helper = SpectrumHelper(SpectrumParams(), lambda _message: None)
        wavelengths = np.linspace(380.0, 780.0, 401)
        intensities = np.full_like(wavelengths, 30000.0)

        *_, status = helper.analyze_quality(wavelengths, intensities, "visible")

        self.assertNotEqual(status, "TOO_WIDE")
        self.assertIn(status, {"GOOD", "OK"})

    def test_wide_peak_finishes_integration_optimization(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=False,
            settle_time_spectrum_s=0.0,
            max_iterations=3,
        )
        helper = SpectrumHelper(params, lambda _message: None)

        class FakeSpectrometer:
            def __init__(self):
                self.intensity_reads = 0

            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                self.intensity_reads += 1
                return np.full(401, 30000.0)

        spectrometer = FakeSpectrometer()

        result = helper.optimize_integration_time(spectrometer)

        self.assertIsNotNone(result)
        self.assertEqual(spectrometer.intensity_reads, 1)

    def test_integration_optimization_previews_every_trial(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=False,
            settle_time_spectrum_s=0.0,
            max_iterations=2,
        )
        helper = SpectrumHelper(params, lambda _message: None)
        previews = []

        class FakeSpectrometer:
            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                return np.full(401, 30000.0)

        helper.optimize_integration_time(
            FakeSpectrometer(),
            preview_callback=lambda iteration, t_int, wavelengths, intensities, status: previews.append(
                (iteration, t_int, len(wavelengths), len(intensities), status)
            ),
        )

        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0][0], 1)
        self.assertEqual(previews[0][2:4], (401, 401))

    def test_integration_optimization_honors_stop_after_preview(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=False,
            settle_time_spectrum_s=0.0,
            max_iterations=3,
        )
        helper = SpectrumHelper(params, lambda _message: None)
        controller = SpectrumMeasurementController()

        class FakeSpectrometer:
            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                return np.full(401, 1000.0)

        with self.assertRaises(SpectrumMeasurementStopped):
            helper.optimize_integration_time(
                FakeSpectrometer(),
                preview_callback=lambda *_args: controller.request_stop(),
                stop_requested=controller.stop_requested,
            )

    def test_integration_optimization_checks_current_before_and_after_trial(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=False,
            settle_time_spectrum_s=0.0,
            max_iterations=1,
        )
        helper = SpectrumHelper(params, lambda _message: None)
        checks = []

        class FakeSpectrometer:
            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                return np.full(401, 30000.0)

        helper.optimize_integration_time(
            FakeSpectrometer(),
            electrical_safety_check=lambda: checks.append("checked"),
        )

        self.assertEqual(checks, ["checked", "checked"])

    def test_integration_optimization_checks_current_between_stale_and_saved_scan(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=True,
            settle_time_spectrum_s=0.0,
            max_iterations=1,
        )
        helper = SpectrumHelper(params, lambda _message: None)
        checks = []

        class FakeSpectrometer:
            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                return np.full(401, 30000.0)

        helper.optimize_integration_time(
            FakeSpectrometer(),
            electrical_safety_check=lambda: checks.append("checked"),
        )

        self.assertEqual(checks, ["checked", "checked", "checked"])

    def test_integration_optimization_aborts_when_current_check_rejects_pixel(self):
        params = SpectrumParams(
            discard_first_scan_after_tint_change=False,
            settle_time_spectrum_s=0.0,
        )
        helper = SpectrumHelper(params, lambda _message: None)

        class FakeSpectrometer:
            def integration_time_micros(self, _value):
                return None

            def wavelengths(self):
                return np.linspace(380.0, 780.0, 401)

            def intensities(self):
                return np.full(401, 30000.0)

        with self.assertRaises(SpectrumPixelRejected):
            helper.optimize_integration_time(
                FakeSpectrometer(),
                electrical_safety_check=lambda: (_ for _ in ()).throw(
                    SpectrumPixelRejected("CURRENT_LIMIT", "pixel burned")
                ),
            )

    def test_spectrum_electrical_status_matches_ivl_thresholds(self):
        self.assertEqual(
            spectrum_electrical_status(10.0, 6.0, 10.0, 0.05),
            "CURRENT_LIMIT",
        )
        self.assertEqual(
            spectrum_electrical_status(6.0, 6.0, 10.0, 0.05),
            "MEASUREMENT_LIMIT",
        )
        self.assertEqual(
            spectrum_electrical_status(0.05, 6.0, 10.0, 0.05),
            "NO_CONTACT",
        )
        self.assertIsNone(
            spectrum_electrical_status(1.0, 6.0, 10.0, 0.05)
        )


if __name__ == "__main__":
    unittest.main()
