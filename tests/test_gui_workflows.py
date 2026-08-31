import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oled_app.gui.app import OLEDModularApp
from oled_app.gui.ivl_window import (
    ivl_series_sequence_from_pixel,
    measure_series_ivl,
)
from oled_app.gui.measurement_menu import (
    pixel_rect_inside_substrate,
    spectrum_queue_cell_text,
    substrate_pixel_ids,
)
from oled_app.gui.spectrum_window import (
    group_pixels_by_substrate,
    handle_rejected_spectrum_data,
    initial_spectrum_start_value,
    measure_spectrum_sequence,
    params_for_pixel_opening,
    queued_spectrum_pixels,
    replacement_pixels_in_quarter,
    resolve_rejected_spectrum_data,
    selected_substrate_pixels,
    sequence_from_start,
    spectrum_selection_visibility,
)
from oled_app.gui.start_screen import setup_pixel_rect
from oled_app.measurements.spectrum import SpectrumParams
from oled_app.series.journal import SeriesJournal
from oled_app.series.layout import build_holder_layout


class _FakeJournal:
    def __init__(self, rows):
        self.rows = rows

    def get_pixel(self, pixel_id):
        return self.rows.get(pixel_id)


class HolderLayoutTests(unittest.TestCase):
    def test_quarters_keep_the_fixed_two_by_two_holder_order(self):
        layout = build_holder_layout(930, 560)

        self.assertLess(layout[2]["number_xy"][0], layout[1]["number_xy"][0])
        self.assertLess(layout[2]["number_xy"][1], layout[3]["number_xy"][1])
        self.assertLess(layout[3]["number_xy"][0], layout[4]["number_xy"][0])

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


class IvlSeriesQueueTests(unittest.TestCase):
    def setUp(self):
        self.pixels = ["P1", "P2", "P3", "P4", "P5", "P6"]
        rows = {
            "P1": {"Last status": "UNKNOWN"},
            "P2": {"Last status": "NONWORKING"},
            "P3": {"Last status": "NO_CONTACT"},
            "P4": {"Last status": "WORKING"},
            "P5": {"Last status": "NONWORKING"},
            "P6": {"Last status": "BURNED"},
        }
        self.app = SimpleNamespace(
            series=SimpleNamespace(journal=_FakeJournal(rows)),
            pixel_ids=lambda: list(self.pixels),
            log=lambda _message: None,
            show_measurement_menu=lambda: None,
        )

    def test_skip_flag_filters_journal_nonworking_and_burned_pixels(self):
        self.assertEqual(
            ivl_series_sequence_from_pixel(
                self.app,
                self.pixels,
                "P1",
                skip_nonworking=True,
            ),
            ["P1", "P3", "P4"],
        )

    def test_manual_selection_rebuilds_queue_after_selected_pixel(self):
        self.assertEqual(
            ivl_series_sequence_from_pixel(
                self.app,
                self.pixels,
                "P3",
                measured=["P1"],
                include_start=False,
            ),
            ["P4", "P5", "P6"],
        )

    def test_series_continues_after_manually_selected_pixel(self):
        with (
            patch(
                "oled_app.gui.ivl_window.messagebox.askyesnocancel",
                side_effect=[False, True, True, True],
            ),
            patch("oled_app.gui.ivl_window.ask_pixel", return_value="P3"),
            patch(
                "oled_app.gui.ivl_window.measure_one_ivl",
                return_value={"status": "WORKING"},
            ) as measure_mock,
        ):
            measure_series_ivl(self.app, SimpleNamespace(), start_pixel="P1")

        self.assertEqual(
            [call.args[1] for call in measure_mock.call_args_list],
            ["P3", "P4", "P5", "P6"],
        )


class SpectrumSubstrateTests(unittest.TestCase):
    def test_last_manual_start_voltage_is_kept_in_next_window(self):
        self.assertEqual(initial_spectrum_start_value({"voltage_start_V": "2.75"}, 2.1), "2.75")

    def setUp(self):
        rows = {
            "CR1_2_3": {
                "Pixel number": 3,
                "Opening voltage (V)": 2.3,
                "Quarter number": 1,
                "Spectrum priority": True,
            },
            "CR1_2_1": {"Pixel number": 1, "Opening voltage (V)": 2.1, "Quarter number": 1},
            "CR1_2_2": {
                "Pixel number": 2,
                "Opening voltage (V)": 2.2,
                "Quarter number": 1,
                "Spectrum priority": True,
            },
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

    def test_substrate_mode_shows_start_pixel_and_substrate(self):
        self.assertEqual(spectrum_selection_visibility("substrate"), (True, True))

    def test_queue_mode_shows_start_pixel_without_substrate(self):
        self.assertEqual(spectrum_selection_visibility("queue"), (True, False))

    def test_substrate_sequence_starts_at_selected_pixel_without_wrapping(self):
        pixels = ["CR1_2_1", "CR1_2_2", "CR1_2_3", "CR1_2_4"]
        self.assertEqual(sequence_from_start(pixels, "CR1_2_3"), ["CR1_2_3", "CR1_2_4"])

    def test_substrate_can_be_limited_to_queued_pixels(self):
        selected = selected_substrate_pixels(
            self.app,
            ["CR1_2_1", "CR1_2_2", "CR1_2_3"],
            queued_only=True,
        )
        self.assertEqual(selected, ["CR1_2_2", "CR1_2_3"])

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


class SpectrumQueueDisplayTests(unittest.TestCase):
    def test_no_contact_deletes_partial_data_without_save_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_file = Path(folder) / "no_contact.csv"
            raw_file.write_text("partial", encoding="utf-8")
            app = SimpleNamespace(log=lambda _message: None)
            result = {
                "status": "NO_CONTACT",
                "raw_files": [raw_file],
            }

            with patch(
                "oled_app.gui.spectrum_window.resolve_rejected_spectrum_data"
            ) as save_dialog:
                note = handle_rejected_spectrum_data(
                    app,
                    "P1",
                    SpectrumParams(),
                    result,
                )

            save_dialog.assert_not_called()
            self.assertFalse(raw_file.exists())
            self.assertFalse(result["rejected_data_kept"])
            self.assertIn("повторная съёмка", note)

    def test_no_contact_prompt_retries_same_pixel_without_replacement_picker(self):
        app = SimpleNamespace(
            log=lambda _message: None,
            show_measurement_menu=lambda: None,
        )
        no_contact = {
            "file": None,
            "discarded": True,
            "status": "NO_CONTACT",
            "stopped_by_user": False,
        }
        success = {
            "file": Path("retry.xlsx"),
            "discarded": False,
            "status": "GOOD",
            "stopped_by_user": False,
        }

        with (
            patch(
                "oled_app.gui.spectrum_window.messagebox.askyesnocancel",
                return_value=True,
            ),
            patch(
                "oled_app.gui.spectrum_window.measure_one_spectrum",
                side_effect=[no_contact, success],
            ) as measure_mock,
            patch(
                "oled_app.gui.spectrum_window.ask_no_contact_retry",
                return_value=True,
            ) as retry_prompt,
            patch(
                "oled_app.gui.spectrum_window.replacement_pixels_in_quarter",
            ) as replacement_picker,
        ):
            measure_spectrum_sequence(
                app,
                ["P1"],
                SpectrumParams(),
                title="Очередь спектров серии",
            )

        retry_prompt.assert_called_once_with(app, "P1")
        replacement_picker.assert_not_called()
        self.assertEqual(
            [call.args[1] for call in measure_mock.call_args_list],
            ["P1", "P1"],
        )

    def test_rejected_spectrum_data_can_be_deleted_from_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_file = Path(folder) / "partial.csv"
            raw_file.write_text("partial", encoding="utf-8")
            app = SimpleNamespace(log=lambda _message: None)
            result = {
                "status": "CURRENT_LIMIT",
                "raw_files": [raw_file],
            }

            with patch(
                "oled_app.gui.spectrum_window.messagebox.askyesno",
                return_value=True,
            ):
                note = resolve_rejected_spectrum_data(
                    app,
                    "P1",
                    SpectrumParams(),
                    result,
                )

            self.assertFalse(raw_file.exists())
            self.assertFalse(result["rejected_data_kept"])
            self.assertIn("удалены", note)

    def test_rejected_spectrum_data_is_kept_when_delete_is_declined(self):
        with tempfile.TemporaryDirectory() as folder:
            raw_file = Path(folder) / "partial.csv"
            raw_file.write_text("partial", encoding="utf-8")
            app = SimpleNamespace(log=lambda _message: None)
            result = {
                "status": "CURRENT_LIMIT",
                "raw_files": [raw_file],
            }

            with (
                patch(
                    "oled_app.gui.spectrum_window.messagebox.askyesno",
                    return_value=False,
                ),
                patch(
                    "oled_app.gui.spectrum_window.save_rejected_spectrum_workbook",
                    return_value=Path(folder) / "diagnostic.xlsx",
                ) as save_mock,
            ):
                note = resolve_rejected_spectrum_data(
                    app,
                    "P1",
                    SpectrumParams(),
                    result,
                )

            self.assertTrue(raw_file.exists())
            self.assertTrue(result["rejected_data_kept"])
            self.assertEqual(result["file"], Path(folder) / "diagnostic.xlsx")
            save_mock.assert_called_once()
            self.assertIn("диагностический XLSX", note)

    def test_rejected_saved_spectrum_still_opens_replacement_picker(self):
        app = SimpleNamespace(
            log=lambda _message: None,
            show_measurement_menu=lambda: None,
        )
        first_result = {
            "file": Path("diagnostic.xlsx"),
            "discarded": True,
            "stopped_by_user": False,
        }
        replacement_result = {
            "file": Path("replacement.xlsx"),
            "discarded": False,
            "stopped_by_user": False,
        }

        with (
            patch(
                "oled_app.gui.spectrum_window.messagebox.askyesnocancel",
                return_value=True,
            ),
            patch(
                "oled_app.gui.spectrum_window.measure_one_spectrum",
                side_effect=[first_result, replacement_result],
            ) as measure_mock,
            patch(
                "oled_app.gui.spectrum_window.replacement_pixels_in_quarter",
                return_value=["P2"],
            ),
            patch(
                "oled_app.gui.ivl_window.ask_pixel",
                return_value="P2",
            ) as picker_mock,
        ):
            measure_spectrum_sequence(
                app,
                ["P1"],
                SpectrumParams(),
                title="Очередь спектров серии",
            )

        picker_mock.assert_called_once()
        self.assertEqual(
            [call.args[1] for call in measure_mock.call_args_list],
            ["P1", "P2"],
        )

    def test_queue_contains_only_marked_unmeasured_pixels(self):
        rows = {
            "P1": {"Spectrum priority": True, "Last spectrum file": ""},
            "P2": {"Spectrum priority": False, "Last spectrum file": ""},
            "P3": {"Spectrum priority": True, "Last spectrum file": "done.xlsx"},
        }
        app = SimpleNamespace(series=SimpleNamespace(journal=_FakeJournal(rows)))

        self.assertEqual(queued_spectrum_pixels(app, ["P1", "P2", "P3"]), ["P1"])

    def test_failed_spectrum_can_be_replaced_only_inside_same_quarter(self):
        rows = [
            {
                "Pixel ID": "Q1_1_1",
                "Quarter number": 1,
                "Opening voltage (V)": 2.0,
                "Last IVL file": "ivl1.xlsx",
                "Last spectrum file": "",
                "Spectrum priority": True,
            },
            {
                "Pixel ID": "Q1_2_1",
                "Quarter number": 1,
                "Opening voltage (V)": 2.1,
                "Last IVL file": "ivl2.xlsx",
                "Last spectrum file": "",
            },
            {
                "Pixel ID": "Q2_1_1",
                "Quarter number": 2,
                "Opening voltage (V)": 2.2,
                "Last IVL file": "ivl3.xlsx",
                "Last spectrum file": "",
            },
        ]
        journal = SimpleNamespace(
            get_pixel=lambda pixel_id: next(
                row for row in rows if row["Pixel ID"] == pixel_id
            ),
            list_pixels=lambda: rows,
        )
        app = SimpleNamespace(series=SimpleNamespace(journal=journal))

        self.assertEqual(
            replacement_pixels_in_quarter(app, "Q1_1_1"),
            ["Q1_2_1"],
        )

    def test_unmeasured_pixel_shows_checkbox_in_spectrum_date_cell(self):
        self.assertEqual(
            spectrum_queue_cell_text({"Spectrum priority": False, "Last spectrum file": ""}),
            "☐ поставить",
        )
        self.assertEqual(
            spectrum_queue_cell_text({"Spectrum priority": True, "Last spectrum file": ""}),
            "☑ в очереди",
        )

    def test_measured_pixel_shows_date_instead_of_checkbox(self):
        row = {
            "Last spectrum file": "spectrum.xlsx",
            "Last spectrum date": "2026-07-27 10:30:00",
            "Spectrum priority": True,
        }
        self.assertEqual(
            spectrum_queue_cell_text(row, 42000),
            "2026-07-27 10:30:00 | max 42000",
        )

    def test_substrate_selection_excludes_pixels_with_spectra(self):
        rows = [
            {"Pixel ID": "CR1_2_1", "Last spectrum file": ""},
            {"Pixel ID": "CR1_2_2", "Last spectrum file": "done.xlsx"},
            {"Pixel ID": "CR1_3_1", "Last spectrum file": ""},
        ]
        self.assertEqual(substrate_pixel_ids(rows, "CR1_2_1"), ["CR1_2_1"])

    def test_journal_batch_queue_only_marks_unmeasured_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = SeriesJournal(Path(folder), {})
            journal.initialize_or_update()
            pixel_ids = [
                str(row["Pixel ID"])
                for row in journal.list_pixels()
                if row.get("Quarter number") == 1 and row.get("Substrate number") == 1
            ][:2]
            journal.set_spectrum_priority(pixel_ids[1], True)
            journal.update_after_measurement(
                "SPECTRUM",
                pixel_ids[1],
                "WORKING",
                Path(folder) / "done.xlsx",
                {},
            )

            changed = journal.set_spectrum_priorities(pixel_ids, True)
            rows = {row["Pixel ID"]: row for row in journal.list_pixels()}

            self.assertEqual(changed, 1)
            self.assertTrue(rows[pixel_ids[0]]["Spectrum priority"])
            self.assertFalse(rows[pixel_ids[1]]["Spectrum priority"])

    def test_rejected_spectrum_does_not_become_last_saved_spectrum(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = SeriesJournal(Path(folder), {})
            journal.initialize_or_update()
            pixel_id = journal.list_pixels()[0]["Pixel ID"]
            journal.set_spectrum_priority(pixel_id, True)

            journal.update_after_measurement(
                "SPECTRUM",
                pixel_id,
                "NO_CONTACT",
                None,
                {},
            )
            row = journal.get_pixel(pixel_id)

            self.assertFalse(row["Last spectrum file"])
            self.assertFalse(row["Spectrum priority"])
            self.assertEqual(row["Last status"], "NO_CONTACT")

    def test_kept_rejected_xlsx_is_recorded_as_taken_spectrum(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            diagnostic = root / "diagnostic.xlsx"
            diagnostic.touch()
            journal = SeriesJournal(root, {})
            journal.initialize_or_update()
            pixel_id = journal.list_pixels()[0]["Pixel ID"]

            journal.update_after_measurement(
                "SPECTRUM",
                pixel_id,
                "CURRENT_LIMIT",
                diagnostic,
                {},
            )
            row = journal.get_pixel(pixel_id)

            self.assertTrue(row["Last spectrum file"])
            self.assertTrue(row["Last spectrum date"])
            self.assertEqual(row["Last status"], "CURRENT_LIMIT")


if __name__ == "__main__":
    unittest.main()
