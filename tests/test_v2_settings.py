from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from oled_app.settings import DEFAULT_APP_SETTINGS
from oled_v2.settings_service import SettingsService, SettingsValidationError


class V2SettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = deepcopy(DEFAULT_APP_SETTINGS)
        self.current["camera"]["photo_quality_settings"] = {"/main/imgsettings/imageformat": "JPEG Fine"}
        self.saved = []
        self.service = SettingsService(
            loader=lambda: deepcopy(self.current),
            saver=self._save,
        )

    def _save(self, settings):
        self.current = deepcopy(settings)
        self.saved.append(deepcopy(settings))

    def test_state_exposes_editable_sections_without_legacy_keys(self) -> None:
        self.current["measurement_units"]["legacy_value"] = 123

        state = self.service.state()

        self.assertNotIn("legacy_value", state["settings"]["measurement_units"])
        self.assertEqual(
            state["settings"]["camera"]["photo_quality_settings"],
            {"/main/imgsettings/imageformat": "JPEG Fine"},
        )

    def test_valid_update_preserves_dynamic_camera_controls(self) -> None:
        payload = self.service.state()["settings"]
        payload["hardware_mode"] = "real"
        payload["camera"]["host"] = "camera.local"
        payload["measurement_units"]["pixel_area_mm2"] = 4.25

        result = self.service.update(payload)

        self.assertEqual(result["settings"]["hardware_mode"], "real")
        self.assertEqual(self.current["camera"]["host"], "camera.local")
        self.assertEqual(self.current["measurement_units"]["pixel_area_mm2"], 4.25)
        self.assertEqual(
            self.current["camera"]["photo_quality_settings"],
            {"/main/imgsettings/imageformat": "JPEG Fine"},
        )
        self.assertEqual(len(self.saved), 1)

    def test_invalid_update_does_not_write(self) -> None:
        payload = self.service.state()["settings"]
        payload["camera"]["port"] = 70000

        with self.assertRaisesRegex(SettingsValidationError, "port"):
            self.service.update(payload)

        self.assertEqual(self.saved, [])

    def test_wifi_profile_is_required_for_auto_connect(self) -> None:
        payload = self.service.state()["settings"]
        payload["camera"]["auto_connect_wifi"] = True
        payload["camera"]["wifi_profile"] = ""

        with self.assertRaisesRegex(SettingsValidationError, "Wi-Fi"):
            self.service.update(payload)

        self.assertEqual(self.saved, [])

    def test_integration_times_must_be_ordered(self) -> None:
        payload = self.service.state()["settings"]
        payload["spectrum_advanced"]["t_int_min_s"] = 2.0
        payload["spectrum_advanced"]["t_int_initial_s"] = 1.0

        with self.assertRaisesRegex(SettingsValidationError, "интегрирования"):
            self.service.update(payload)

    def test_saved_values_feed_v2_measurement_defaults(self) -> None:
        settings = deepcopy(DEFAULT_APP_SETTINGS)
        settings["measurement_units"]["pixel_area_mm2"] = 3.5
        settings["ivl_advanced"]["photodiode_threshold_uA"] = 1.25
        settings["spectrum_advanced"]["target_intensity"] = 32100.0
        settings["stability_advanced"]["voltage_step_max"] = 0.07

        with patch("oled_v2.ivl.load_app_settings", return_value=settings):
            from oled_v2.ivl import default_params as ivl_defaults
            self.assertEqual(ivl_defaults().photodiode_threshold_uA, 1.25)
            self.assertEqual(ivl_defaults().pixel_area_mm2, 3.5)
        with patch("oled_v2.spectrum.load_app_settings", return_value=settings):
            from oled_v2.spectrum import default_params as spectrum_defaults
            self.assertEqual(spectrum_defaults().target_intensity, 32100.0)
            self.assertEqual(spectrum_defaults().pixel_area_mm2, 3.5)
        with patch("oled_v2.stability.load_app_settings", return_value=settings):
            from oled_v2.stability import default_params as stability_defaults
            self.assertEqual(stability_defaults().voltage_step_max, 0.07)
            self.assertEqual(stability_defaults().pixel_area_mm2, 3.5)


if __name__ == "__main__":
    unittest.main()
