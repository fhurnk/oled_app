import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from oled_app.gui.report_window import (
    collect_report_spectrum_candidates,
    selected_report_candidates,
)
from oled_app.reports.origin_report import (
    MeasurementPath,
    SpectrumRecord,
    collect_spectrum_records,
    parse_args,
)


class _Var:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


def _write_spectrum(path: Path, voltage: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Processed counts per s"
    ws.cell(row=1, column=1, value="V set (V)")
    ws.cell(row=1, column=2, value=voltage)
    wb.save(path)


class ReportWindowSelectionTests(unittest.TestCase):
    def test_candidates_are_grouped_by_series_then_substrate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            series_folder = Path(temp_dir)
            spectra_root = series_folder / "measurements" / "02_SPECTRA" / "2026-07-18" / "CG1"
            _write_spectrum(spectra_root / "CG1_1" / "CG1_1_1" / "SPECTRUM_CG1_1_1.xlsx")
            _write_spectrum(spectra_root / "CG1_2" / "CG1_2_1" / "SPECTRUM_CG1_2_1.xlsx")
            app = SimpleNamespace(series=SimpleNamespace(series_folder=series_folder))

            candidates = collect_report_spectrum_candidates(app, "2026-07-18")

            self.assertEqual(set(candidates), {"CG1"})
            self.assertEqual(set(candidates["CG1"]), {"CG1_1", "CG1_2"})
            self.assertEqual(set(candidates["CG1"]["CG1_2"]), {"CG1_2_1"})

    def test_selected_pixel_is_limited_to_selected_substrate(self):
        candidates = {
            "CG1": {
                "CG1_1": {"CG1_1_1": {"file": Path("one.xlsx"), "voltages": [2.0]}},
                "CG1_2": {"CG1_2_3": {"file": Path("two.xlsx"), "voltages": [2.1]}},
            }
        }

        selected = selected_report_candidates(
            candidates,
            {"CG1": _Var("CG1_2")},
            {"CG1": _Var("CG1_2_3")},
        )

        self.assertEqual(set(selected), {"CG1_2_3"})
        self.assertEqual(selected["CG1_2_3"]["series"], "CG1")
        self.assertEqual(selected["CG1_2_3"]["subseries"], "CG1_2")


class ReportBuilderSelectionTests(unittest.TestCase):
    def test_series_selection_keeps_one_substrate_and_pixel(self):
        timestamp = datetime(2026, 7, 18, 12, 0, 0)
        paths = {
            "CG1_1_1": MeasurementPath(Path("one.xlsx"), "2026-07-18", "CG1", "CG1_1", "CG1_1_1", timestamp),
            "CG1_2_3": MeasurementPath(Path("two.xlsx"), "2026-07-18", "CG1", "CG1_2", "CG1_2_3", timestamp),
        }

        def make_record(meta, _sheet_name, _warnings):
            return SpectrumRecord(
                meta.path,
                meta.date_dir,
                meta.series,
                meta.subseries,
                meta.pixel,
                [2.0],
                [500.0],
                [[100.0]],
            )

        warnings = []
        with patch("oled_app.reports.origin_report.latest_by_pixel", return_value=paths), patch(
            "oled_app.reports.origin_report.read_spectrum_record",
            side_effect=make_record,
        ):
            records = collect_spectrum_records(
                Path("spectra"),
                {},
                "Processed counts per s",
                True,
                warnings,
                "2026-07-18",
                {"CG1": "CG1_2_3"},
            )

        self.assertEqual([record.pixel for record in records], ["CG1_2_3"])
        self.assertEqual(records[0].subseries, "CG1_2")
        self.assertEqual(warnings, [])

    def test_cli_accepts_series_pixel_selection(self):
        args = parse_args(["--spectrum-series-pixel", "CG1=CG1_2_3"])
        self.assertEqual(args.spectrum_series_pixel, ["CG1=CG1_2_3"])


if __name__ == "__main__":
    unittest.main()
