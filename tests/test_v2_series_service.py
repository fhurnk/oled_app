from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from oled_app.constants import CONFIG_FILE, JOURNAL_FILE
from oled_v2.series_service import (
    SeriesNotFoundError,
    SeriesService,
    SeriesValidationError,
)


def series_payload(root: Path) -> dict:
    return {
        "root": str(root),
        "deposition_date": "2026-07-31",
        "keyword": "alpha",
        "series_led_color": "green",
        "quarter_bases": {"1": "A", "2": "B", "3": "C", "4": "D"},
        "quarter_descriptions": {
            "1": "reference",
            "2": "transport",
            "3": "emission",
            "4": "control",
        },
    }


class V2SeriesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "Серии OLED"
        self.root.mkdir()
        self.service = SeriesService(default_root=self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_list_open_and_edit_preserve_existing_contracts(self) -> None:
        state = self.service.create_series(series_payload(self.root))
        active = state["active"]

        self.assertIsNotNone(active)
        self.assertEqual(len(active["pixels"]), 48)
        self.assertEqual(active["metrics"]["substrates"], 12)
        self.assertEqual(active["quarters"][0]["code"], "AG")
        folder = Path(active["path"])
        self.assertTrue((folder / CONFIG_FILE).is_file())
        self.assertTrue((folder / JOURNAL_FILE).is_file())

        self.service.close_series()
        listed = self.service.state()
        self.assertEqual(len(listed["recent"]), 1)
        reopened = self.service.open_series(folder)
        self.assertEqual(reopened["active"]["path"], str(folder.resolve()))

        updated_payload = series_payload(self.root)
        updated_payload["keyword"] = "edited"
        updated = self.service.update_active(updated_payload)
        self.assertEqual(updated["active"]["keyword"], "edited")
        self.assertEqual(len(updated["active"]["pixels"]), 48)

    def test_spectrum_queue_supports_pixel_and_substrate_scope(self) -> None:
        state = self.service.create_series(series_payload(self.root))
        first = state["active"]["pixels"][0]["pixel_id"]

        one = self.service.set_spectrum_priority(first, True)
        self.assertEqual(one["queue_update"]["changed"], 1)
        self.assertEqual(one["active"]["metrics"]["spectrum_queue"], 1)

        substrate = self.service.set_spectrum_priority(first, True, "substrate")
        self.assertEqual(substrate["queue_update"]["requested"], 4)
        self.assertEqual(substrate["queue_update"]["changed"], 3)
        self.assertEqual(substrate["active"]["metrics"]["spectrum_queue"], 4)

        cleared = self.service.set_spectrum_priority(first, False, "substrate")
        self.assertEqual(cleared["queue_update"]["changed"], 4)
        self.assertEqual(cleared["active"]["metrics"]["spectrum_queue"], 0)

    def test_refresh_builds_thumbnail_and_returns_history(self) -> None:
        state = self.service.create_series(series_payload(self.root))
        pixel_id = state["active"]["pixels"][0]["pixel_id"]
        manager = self.service._active
        assert manager is not None
        workbook_path = manager.series_folder / "measurements" / "ivl-test.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Cycle_1"
        sheet["A1"] = "Cycle 1 | WORKING"
        sheet.append([])
        sheet.append(
            [
                "Voltage OLED / LED measured (V)",
                "Current OLED / LED (mA)",
                "Photodiode current (uA)",
            ]
        )
        sheet.append([0.0, 0.0, 0.0])
        sheet.append([3.0, 1.2, 0.6])
        workbook.save(workbook_path)
        workbook.close()
        manager.journal.update_after_measurement(
            "IVL",
            pixel_id,
            "WORKING",
            workbook_path,
            params={"source": "test"},
            opening_voltage=2.7,
            max_current_mA=1.2,
            max_photo_uA=0.6,
        )

        refreshed = self.service.refresh_active()
        row = next(item for item in refreshed["active"]["pixels"] if item["pixel_id"] == pixel_id)
        self.assertTrue(row["thumbnail_available"])
        self.assertEqual(refreshed["active"]["metrics"]["history"], 1)
        self.assertEqual(refreshed["active"]["history"][0]["type"], "IVL")
        self.assertTrue(self.service.thumbnail_for_pixel(pixel_id).is_file())

    def test_invalid_root_and_date_return_operator_facing_errors(self) -> None:
        with self.assertRaises(SeriesNotFoundError):
            self.service.set_root(self.root / "missing")

        payload = series_payload(self.root)
        payload["deposition_date"] = "31.07.2026"
        with self.assertRaisesRegex(SeriesValidationError, "ГГГГ-ММ-ДД"):
            self.service.create_series(payload)

    def test_white_color_and_half_description_scope_are_persisted(self) -> None:
        payload = series_payload(self.root)
        payload["series_led_color"] = "white"
        payload["description_scope"] = "half"
        payload["quarter_layout"] = {
            "top_left": 4,
            "top_right": 1,
            "bottom_left": 2,
            "bottom_right": 3,
        }
        payload["quarter_descriptions"] = {
            "1": "will be replaced",
            "2": "bottom",
            "3": "will be replaced",
            "4": "top",
        }

        active = self.service.create_series(payload)["active"]

        self.assertEqual(active["series_led_color"], "white")
        self.assertEqual(active["description_scope"], "half")
        self.assertEqual(
            active["quarter_layout"],
            payload["quarter_layout"],
        )
        self.assertEqual([item["code"] for item in active["quarters"]], ["AW", "BW", "CW", "DW"])
        self.assertEqual(
            [item["description"] for item in active["quarters"]],
            ["top", "bottom", "bottom", "top"],
        )


if __name__ == "__main__":
    unittest.main()
