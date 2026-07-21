import unittest
from types import SimpleNamespace

from oled_app.gui.app import OLEDModularApp
from oled_app.gui.measurement_menu import pixel_rect_inside_substrate
from oled_app.gui.spectrum_window import (
    group_pixels_by_substrate,
    initial_spectrum_start_value,
    params_for_pixel_opening,
)
from oled_app.gui.start_screen import setup_pixel_rect
from oled_app.measurements.spectrum import SpectrumParams
from oled_app.series.layout import build_holder_layout


class _FakeJournal:
    def __init__(self, rows):
        self.rows = rows

    def get_pixel(self, pixel_id):
        return self.rows.get(pixel_id)


class HolderLayoutTests(unittest.TestCase):
    def test_pixels_use_physical_two_by_two_order(self):
        for rect_function in (setup_pixel_rect, pixel_rect_inside_substrate):
            with self.subTest(rect_function=rect_function.__name__):
                rectangles = {
                    pixel: rect_function(0, 0, 100, 60, pixel)
                    for pixel in range(1, 5)
                }
                self.assertEqual(rectangles[1][0], rectangles[4][0])
                self.assertEqual(rectangles[2][0], rectangles[3][0])
                self.assertEqual(rectangles[1][1], rectangles[2][1])
                self.assertEqual(rectangles[4][1], rectangles[3][1])
                self.assertLess(rectangles[1][0], rectangles[2][0])
                self.assertLess(rectangles[1][1], rectangles[4][1])

    def test_physical_substrate_order_matches_holder(self):
        width = 930
        layout = build_holder_layout(width, 560)
        center = width / 2

        for quarter in (1, 2, 3, 4):
            substrates = {item["substrate_number"]: item for item in layout[quarter]["substrates"]}
            self.assertEqual(substrates[1]["x"], substrates[2]["x"])
            self.assertLess(
                abs((substrates[2]["x"] + substrates[2]["w"] / 2) - center),
                abs((substrates[3]["x"] + substrates[3]["w"] / 2) - center),
            )
            self.assertEqual(substrates[2]["y"], substrates[3]["y"])

        self.assertLess(layout[2]["substrates"][0]["y"], layout[2]["substrates"][1]["y"])
        self.assertGreater(layout[3]["substrates"][0]["y"], layout[3]["substrates"][1]["y"])


class GuiLifecycleTests(unittest.TestCase):
    def test_clear_forgets_destroyed_log_widget(self):
        child = SimpleNamespace(destroy=lambda: None)
        app = SimpleNamespace(log_widget=object(), winfo_children=lambda: [child])

        OLEDModularApp.clear(app)

        self.assertIsNone(app.log_widget)

    def test_log_ignores_widget_that_no_longer_exists(self):
        dead_widget = SimpleNamespace(winfo_exists=lambda: False)
        app = SimpleNamespace(log_widget=dead_widget)

        OLEDModularApp.log(app, "Создана серия")

        self.assertIsNone(app.log_widget)


class SpectrumSubstrateTests(unittest.TestCase):
    def test_last_manual_start_voltage_is_kept_in_next_window(self):
        self.assertEqual(initial_spectrum_start_value({"voltage_start_V": "2.75"}, 2.1), "2.75")

    def setUp(self):
        rows = {
            "CR1_2_3": {"Pixel number": 3, "Opening voltage (V)": 2.3, "Quarter number": 1},
            "CR1_2_1": {"Pixel number": 1, "Opening voltage (V)": 2.1, "Quarter number": 1},
            "CR1_2_2": {"Pixel number": 2, "Opening voltage (V)": 2.2, "Quarter number": 1},
            "CR1_3_1": {"Pixel number": 1, "Opening voltage (V)": 2.4, "Quarter number": 1},
        }
        series = SimpleNamespace(
            journal=_FakeJournal(rows),
            config={"quarter_led_colors": {"1": "red"}},
            luminance_coefficient_for_pixel=lambda _pixel_id, _settings: 7.5,
        )
        self.app = SimpleNamespace(series=series, app_settings={})

    def test_pixels_are_grouped_and_sorted_within_one_substrate(self):
        pixels = ["CR1_2_3", "CR1_3_1", "CR1_2_1", "CR1_2_2"]
        groups = group_pixels_by_substrate(self.app, pixels)
        self.assertEqual(groups["CR1_2"], ["CR1_2_1", "CR1_2_2", "CR1_2_3"])
        self.assertEqual(groups["CR1_3"], ["CR1_3_1"])

    def test_each_pixel_uses_its_own_opening_voltage(self):
        params = SpectrumParams(
            voltage_start=1.0,
            opening_voltage=1.0,
            voltage_start_source="opening",
            led_type="auto",
        )
        updated = params_for_pixel_opening(self.app, "CR1_2_3", params)
        self.assertEqual(updated.voltage_start, 2.3)
        self.assertEqual(updated.opening_voltage, 2.3)
        self.assertEqual(updated.luminance_cd_m2_per_uA, 7.5)
        self.assertEqual(updated.led_type, "red")

    def test_manual_start_is_shared_but_opening_is_recorded(self):
        params = SpectrumParams(voltage_start=1.7, opening_voltage=1.0, voltage_start_source="manual")
        updated = params_for_pixel_opening(self.app, "CR1_2_2", params)
        self.assertEqual(updated.voltage_start, 1.7)
        self.assertEqual(updated.opening_voltage, 2.2)


if __name__ == "__main__":
    unittest.main()
