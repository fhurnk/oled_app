from __future__ import annotations

import unittest

from oled_app.measurements.spectrum import SpectrumHelper
from oled_app.series.metadata import (
    base_luminance_coefficient_for_color,
    description_scope_groups,
    led_color_label,
    normalize_led_color,
    normalize_quarter_payload,
    quarter_base,
    quarter_code,
    quarter_led_color,
)


class LedColorMetadataTests(unittest.TestCase):
    def test_white_alias_label_suffix_and_legacy_name_are_supported(self) -> None:
        self.assertEqual(normalize_led_color("w"), "white")
        self.assertEqual(normalize_led_color("белый"), "white")
        self.assertEqual(led_color_label("white"), "Белый (W)")

        config = {"quarter_names": {"1": "CW"}}
        self.assertEqual(quarter_base(config, 1), "C")
        self.assertEqual(quarter_led_color(config, 1), "white")

        payload = normalize_quarter_payload(
            {str(number): "C" for number in range(1, 5)},
            {},
            {"1": "white"},
        )
        self.assertEqual(payload["series_led_color"], "white")
        self.assertEqual(quarter_code(payload, 1), "CW")

    def test_white_has_separate_luminance_coefficient_and_visible_peak_range(self) -> None:
        settings = {
            "measurement_units": {
                "luminance_white_cd_m2_per_uA": 4.25,
            }
        }
        self.assertEqual(base_luminance_coefficient_for_color(settings, "white"), 4.25)
        self.assertEqual(SpectrumHelper.peak_range([300.0, 1000.0], "white"), (380, 780))


class DescriptionScopeTests(unittest.TestCase):
    def test_half_scope_expands_top_and_bottom_descriptions(self) -> None:
        payload = normalize_quarter_payload(
            {str(number): "Q" for number in range(1, 5)},
            {"1": "ignored", "2": "top", "3": "bottom", "4": "ignored"},
            {"1": "red"},
            "half",
        )

        self.assertEqual(description_scope_groups("half"), ((2, 1), (3, 4)))
        self.assertEqual(
            payload["quarter_descriptions"],
            {"1": "top", "2": "top", "3": "bottom", "4": "bottom"},
        )
        self.assertEqual(payload["description_scope"], "half")

    def test_half_scope_can_expand_left_and_right_descriptions(self) -> None:
        payload = normalize_quarter_payload(
            {str(number): "Q" for number in range(1, 5)},
            {"1": "right", "2": "left", "3": "ignored", "4": "ignored"},
            {"1": "white"},
            "half",
            "left_right",
        )

        self.assertEqual(payload["half_orientation"], "left_right")
        self.assertEqual(description_scope_groups("half", "left_right"), ((2, 3), (1, 4)))
        self.assertEqual(
            payload["quarter_descriptions"],
            {"1": "right", "2": "left", "3": "left", "4": "right"},
        )

    def test_substrate_scope_expands_one_description_to_all_quarters(self) -> None:
        payload = normalize_quarter_payload(
            {str(number): "Q" for number in range(1, 5)},
            {"1": "shared", "2": "two", "3": "three", "4": "four"},
            {"1": "white"},
            "substrate",
        )

        self.assertEqual(
            payload["quarter_descriptions"],
            {str(number): "shared" for number in range(1, 5)},
        )
        self.assertEqual(payload["description_scope"], "substrate")


if __name__ == "__main__":
    unittest.main()
