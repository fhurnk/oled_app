from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import numpy as np

from oled_app.hardware.probe import probe_spectrometer
from oled_app.gui.spectrum_window import initial_spectrum_start_value
from oled_app.measurements.spectrum import (
    SpectrumHelper,
    SpectrumMeasurementController,
    SpectrumMeasurementStopped,
    SpectrumParams,
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


if __name__ == "__main__":
    unittest.main()
