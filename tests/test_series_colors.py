import unittest

from oled_app.gui.start_screen import collect_quarter_payload
from oled_app.series.metadata import (
    normalize_quarter_payload,
    quarter_code,
    quarter_led_color,
)


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class QuarterColorTests(unittest.TestCase):
    def test_quarter_color_overrides_legacy_series_color(self):
        config = {
            "series_led_color": "red",
            "quarter_led_colors": {
                "1": "red",
                "2": "green",
                "3": "blue",
                "4": "green",
            },
        }

        self.assertEqual(quarter_led_color(config, 1), "red")
        self.assertEqual(quarter_led_color(config, 2), "green")
        self.assertEqual(quarter_led_color(config, 3), "blue")
        self.assertEqual(quarter_led_color(config, 4), "green")

    def test_normalized_payload_preserves_each_quarter_color(self):
        payload = normalize_quarter_payload(
            {str(q): "C" for q in range(1, 5)},
            {str(q): "" for q in range(1, 5)},
            {"1": "red", "2": "green", "3": "blue", "4": "green"},
        )

        self.assertEqual(
            payload["quarter_led_colors"],
            {"1": "red", "2": "green", "3": "blue", "4": "green"},
        )
        self.assertEqual(payload["series_led_color"], "red")
        self.assertEqual(
            [quarter_code(payload, q) for q in range(1, 5)],
            ["CR", "CG", "CB", "CG"],
        )

    def test_missing_quarter_colors_inherit_first_color(self):
        payload = normalize_quarter_payload(
            {str(q): "C" for q in range(1, 5)},
            {},
            {"1": "green"},
        )

        self.assertEqual(
            payload["quarter_led_colors"],
            {str(q): "green" for q in range(1, 5)},
        )

    def test_gui_payload_collects_color_from_each_quarter(self):
        labels = ["Красный (R)", "Зеленый (G)", "Синий (B)", "Зеленый (G)"]
        quarter_vars = {
            str(q): {
                "base": _Value("C"),
                "description": _Value(f"quarter {q}"),
                "color": _Value(labels[q - 1]),
            }
            for q in range(1, 5)
        }

        bases, descriptions, colors = collect_quarter_payload(quarter_vars)

        self.assertEqual(bases, {str(q): "C" for q in range(1, 5)})
        self.assertEqual(descriptions["3"], "quarter 3")
        self.assertEqual(
            colors,
            {"1": "red", "2": "green", "3": "blue", "4": "green"},
        )


if __name__ == "__main__":
    unittest.main()
