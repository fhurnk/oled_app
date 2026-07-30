import unittest

from oled_app.measurements.ivl import detect_opening_voltage


def point(voltage: float, photodiode_current_uA: float, measured_voltage=None):
    row = {
        "Voltage set (V)": voltage,
        "Photodiode current (uA)": photodiode_current_uA,
    }
    if measured_voltage is not None:
        row["Voltage OLED / LED measured (V)"] = measured_voltage
    return row


class OpeningVoltageDetectionTests(unittest.TestCase):
    def test_ignores_single_threshold_spike(self):
        data = [
            point(1.0, 0.1),
            point(1.1, 0.6),
            point(1.2, 0.2),
            point(1.3, 0.5),
            point(1.4, 0.6),
            point(1.5, 0.7),
        ]

        self.assertEqual(detect_opening_voltage(data, 0.5, 2), 1.3)

    def test_confirmation_points_are_points_after_candidate(self):
        data = [
            point(2.0, 0.5),
            point(2.1, 0.5),
            point(2.2, 0.5),
        ]

        self.assertEqual(detect_opening_voltage(data, 0.5, 2), 2.0)
        self.assertIsNone(detect_opening_voltage(data, 0.5, 3))

    def test_uses_measured_voltage_at_confirmed_candidate(self):
        data = [
            point(1.0, 0.4),
            point(1.1, 0.5, measured_voltage=1.08),
            point(1.2, 0.7),
        ]

        self.assertEqual(detect_opening_voltage(data, 0.5, 1), 1.08)

    def test_rejects_invalid_detection_settings(self):
        with self.assertRaises(ValueError):
            detect_opening_voltage([], -0.1, 5)
        with self.assertRaises(ValueError):
            detect_opening_voltage([], 0.5, -1)


if __name__ == "__main__":
    unittest.main()
