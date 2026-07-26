"""Spectrum sensitivity correction and quarter-level integral calibration."""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.styles import Font, PatternFill

from oled_app.utils import (
    as_float_or_none,
    autosize_columns,
    now_str,
    safe_filename,
    style_header_row,
    timestamp_for_file,
)


DEFAULT_SENSITIVITY_CSV = Path(__file__).resolve().parent.parent / "data" / "spectral_sensitivity.csv"


@dataclass(frozen=True)
class SpectralIntegral:
    """Corrected spectral integrals on the common CIE/BPW34 grid."""

    shape_integral: float
    weighted_integral: float
    peak_intensity: float
    wavelength_min_nm: float
    wavelength_max_nm: float
    point_count: int


@dataclass(frozen=True)
class QuarterIntegralCalibration:
    """Origin-constrained fit converting weighted counts/s to cd/m²."""

    integral_coefficient: float
    coefficient: float
    geometric_coefficient: float
    effective_coefficient: float
    points_used: int
    r_squared: Optional[float]
    source_pixel: str
    source_file: str
    calculated_at: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=8)
def load_sensitivity_curve(path_text: str = "") -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load and validate the machine-readable CIE/BPW34 sensitivity table."""

    path = Path(path_text) if path_text else DEFAULT_SENSITIVITY_CSV
    wavelengths: List[float] = []
    cie: List[float] = []
    bpw34: List[float] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            wavelength = as_float_or_none(row.get("wavelength_nm"))
            cie_value = as_float_or_none(row.get("cie_v_lambda"))
            bpw_value = as_float_or_none(row.get("bpw34_relative_response"))
            if wavelength is None or cie_value is None or bpw_value is None or bpw_value <= 0:
                continue
            wavelengths.append(float(wavelength))
            cie.append(float(cie_value))
            bpw34.append(float(bpw_value))
    if len(wavelengths) < 2:
        raise ValueError(f"В таблице чувствительности недостаточно точек: {path}")
    order = np.argsort(np.asarray(wavelengths, dtype=np.float64))
    wl = np.asarray(wavelengths, dtype=np.float64)[order]
    cie_values = np.asarray(cie, dtype=np.float64)[order]
    bpw_values = np.asarray(bpw34, dtype=np.float64)[order]
    return wl, cie_values, bpw_values


def _trapezoid(values: np.ndarray, wavelengths: np.ndarray) -> float:
    trapezoid = getattr(np, "trapezoid", None)
    if trapezoid is not None:
        return float(trapezoid(values, wavelengths))
    return float(np.trapz(values, wavelengths))


def calculate_spectral_integrals(
    wavelengths: Sequence[float],
    intensities_counts_per_s: Sequence[float],
    sensitivity_csv: Optional[Path] = None,
) -> SpectralIntegral:
    """Normalize a processed spectrum and integrate ``spectrum * CIE / BPW34``.

    ``weighted_integral`` keeps the absolute counts/s scale and is therefore
    suitable for luminance fitting. ``shape_integral`` follows the user's
    manual check: the spectrum is first normalized to its maximum.
    """

    wl = np.asarray(wavelengths, dtype=np.float64)
    intensity = np.asarray(intensities_counts_per_s, dtype=np.float64)
    if wl.shape != intensity.shape or wl.ndim != 1:
        raise ValueError("Длины волн и интенсивности должны быть одномерными массивами одинаковой длины.")

    ref_wl, ref_cie, ref_bpw = load_sensitivity_curve(str(sensitivity_csv or ""))
    mask = (
        np.isfinite(wl)
        & np.isfinite(intensity)
        & (wl >= ref_wl[0])
        & (wl <= ref_wl[-1])
    )
    if int(np.count_nonzero(mask)) < 2:
        raise ValueError("Спектр не пересекается с диапазоном таблицы чувствительности.")

    wl = wl[mask]
    intensity = intensity[mask]
    order = np.argsort(wl)
    wl = wl[order]
    intensity = intensity[order]
    unique_wl, unique_indices = np.unique(wl, return_index=True)
    wl = unique_wl
    intensity = intensity[unique_indices]

    cie = np.interp(wl, ref_wl, ref_cie)
    bpw34 = np.interp(wl, ref_wl, ref_bpw)
    valid = np.isfinite(cie) & np.isfinite(bpw34) & (bpw34 > 0)
    wl = wl[valid]
    intensity = intensity[valid]
    correction = cie[valid] / bpw34[valid]
    if wl.size < 2:
        raise ValueError("После интерполяции чувствительности осталось недостаточно точек.")

    peak = float(np.nanmax(intensity))
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("Максимум обработанного спектра должен быть положительным.")
    normalized = intensity / peak
    return SpectralIntegral(
        shape_integral=_trapezoid(normalized * correction, wl),
        weighted_integral=_trapezoid(intensity * correction, wl),
        peak_intensity=peak,
        wavelength_min_nm=float(wl[0]),
        wavelength_max_nm=float(wl[-1]),
        point_count=int(wl.size),
    )


def integral_luminance_cd_m2(
    weighted_integral: Any,
    integral_coefficient: Any,
    geometric_coefficient: Any,
    quarter_coefficient: Any = 1.0,
) -> Optional[float]:
    integral = as_float_or_none(weighted_integral)
    coefficient = as_float_or_none(integral_coefficient)
    geometry = as_float_or_none(geometric_coefficient)
    quarter = as_float_or_none(quarter_coefficient)
    if integral is None or coefficient is None or geometry is None or quarter is None:
        return None
    return float(integral) * float(coefficient) * float(quarter) * float(geometry)


def _find_header_row(ws, required_header: str, limit: int = 60) -> Tuple[int, Dict[str, int]]:
    for row in range(1, min(ws.max_row, limit) + 1):
        headers = {
            str(ws.cell(row, column).value or "").strip(): column
            for column in range(1, ws.max_column + 1)
        }
        if required_header in headers:
            return row, headers
    raise ValueError(f"В листе {ws.title} не найден столбец {required_header!r}.")


def read_spectrum_integral_points(workbook_path: Path) -> List[Dict[str, Any]]:
    """Read per-voltage integral and photodiode pairs from a spectrum workbook."""

    wb = load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        ws_sum = wb["Сводка"]
        summary_header_row, summary_headers = _find_header_row(ws_sum, "I photodiode (uA)")
        summary_by_point: Dict[int, Dict[str, Any]] = {}
        for row in range(summary_header_row + 1, ws_sum.max_row + 1):
            point = as_float_or_none(ws_sum.cell(row, summary_headers.get("Point", 1)).value)
            if point is None:
                continue
            summary_by_point[int(point)] = {
                "point": int(point),
                "voltage_V": as_float_or_none(
                    ws_sum.cell(row, summary_headers.get("V set (V)", 2)).value
                ),
                "photodiode_uA": as_float_or_none(
                    ws_sum.cell(row, summary_headers["I photodiode (uA)"]).value
                ),
                "photodiode_luminance_cd_m2": as_float_or_none(
                    ws_sum.cell(row, summary_headers.get("Luminance (cd/m^2)", 8)).value
                ),
                "status": str(
                    ws_sum.cell(row, summary_headers.get("Status", 13)).value or ""
                ),
            }

        if "Processed counts per s" not in wb.sheetnames:
            raise ValueError("В книге нет листа 'Processed counts per s'.")
        ws_processed = wb["Processed counts per s"]
        data_header_row, _processed_headers = _find_header_row(
            ws_processed,
            "Wavelength (nm)",
        )
        wavelength_rows = list(range(data_header_row + 1, ws_processed.max_row + 1))
        wavelengths = [
            as_float_or_none(ws_processed.cell(row, 1).value)
            for row in wavelength_rows
        ]
        rows = []
        for column in range(2, ws_processed.max_column + 1):
            point_value = as_float_or_none(ws_processed.cell(2, column).value)
            if point_value is None:
                continue
            point = int(point_value)
            pairs = [
                (float(wavelength), float(intensity))
                for row, wavelength in zip(wavelength_rows, wavelengths)
                if wavelength is not None
                for intensity in [as_float_or_none(ws_processed.cell(row, column).value)]
                if intensity is not None
            ]
            if len(pairs) < 2:
                continue
            spectral = calculate_spectral_integrals(
                [pair[0] for pair in pairs],
                [pair[1] for pair in pairs],
            )
            row_info = dict(summary_by_point.get(point, {"point": point}))
            row_info.update(
                {
                    "shape_integral": spectral.shape_integral,
                    "weighted_integral": spectral.weighted_integral,
                    "integral_luminance_cd_m2": None,
                }
            )
            rows.append(row_info)
        return rows
    finally:
        wb.close()


def fit_quarter_integral_coefficient(
    points: Iterable[Dict[str, Any]],
    geometric_coefficient: float,
    source_pixel: str,
    source_file: str,
    integral_coefficient: float = 1.0,
) -> QuarterIntegralCalibration:
    """Fit luminance against the corrected absolute spectral integral."""

    geometry = float(geometric_coefficient)
    if geometry <= 0 or not math.isfinite(geometry):
        raise ValueError("Геометрический коэффициент должен быть положительным.")
    configured_integral = float(integral_coefficient)
    if configured_integral <= 0 or not math.isfinite(configured_integral):
        raise ValueError("Интегральный коэффициент должен быть положительным.")

    x_values: List[float] = []
    y_values: List[float] = []
    rejected_statuses = {"FAILED", "SATURATED", "NEEDS_REVIEW", "STOPPED", "NO_PEAK"}
    for point in points:
        status = str(point.get("status") or "").strip().upper()
        x = as_float_or_none(point.get("weighted_integral"))
        y = as_float_or_none(point.get("photodiode_luminance_cd_m2"))
        if status in rejected_statuses or x is None or y is None or x <= 0 or y <= 0:
            continue
        x_values.append(float(x))
        y_values.append(float(y))
    if len(x_values) < 2:
        raise ValueError("Для калибровки нужны минимум две пригодные точки спектра с разной светимостью.")

    x_arr = np.asarray(x_values, dtype=np.float64)
    y_arr = np.asarray(y_values, dtype=np.float64)
    base_x = x_arr * configured_integral * geometry
    coefficient = float(np.dot(base_x, y_arr) / np.dot(base_x, base_x))
    effective = configured_integral * coefficient * geometry
    predicted = x_arr * effective
    residual = float(np.sum((y_arr - predicted) ** 2))
    centered = float(np.sum((y_arr - float(np.mean(y_arr))) ** 2))
    r_squared = None if centered <= 0 else 1.0 - residual / centered
    return QuarterIntegralCalibration(
        integral_coefficient=configured_integral,
        coefficient=coefficient,
        geometric_coefficient=geometry,
        effective_coefficient=effective,
        points_used=len(x_values),
        r_squared=r_squared,
        source_pixel=str(source_pixel),
        source_file=str(source_file),
        calculated_at=now_str(),
    )


def spectral_recalculation_output_path(source_workbook: Path, pixel_id: str) -> Path:
    source_workbook = Path(source_workbook)
    base_stem = f"SPECTRAL_RECALC_{safe_filename(pixel_id)}_{timestamp_for_file()}"
    candidate = source_workbook.with_name(f"{base_stem}.xlsx")
    suffix = 2
    while candidate.exists():
        candidate = candidate.with_name(f"{base_stem}_{suffix}.xlsx")
        suffix += 1
    return candidate


def create_spectral_recalculation_workbook(
    output_path: Path,
    source_workbook: Path,
    pixel_id: str,
    quarter_number: int,
    points: Iterable[Dict[str, Any]],
    calibration: QuarterIntegralCalibration,
    sensitivity_csv: Optional[Path] = None,
    effective_photodiode_coefficient: Optional[float] = None,
) -> Path:
    """Save on-demand CIE/BPW34 recalculation without modifying the source."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    points = list(points)
    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"
    ws["A1"] = "Спектральный пересчёт CIE / BPW34"
    ws["A1"].font = Font(bold=True, size=14)
    effective_photo = as_float_or_none(effective_photodiode_coefficient)
    base_color_coefficient = (
        None
        if effective_photo is None or calibration.geometric_coefficient == 0
        else effective_photo / calibration.geometric_coefficient
    )
    metadata = [
        ("Source spectrum", str(Path(source_workbook).resolve())),
        ("Pixel", pixel_id),
        ("Quarter", int(quarter_number)),
        ("Recalculated", now_str()),
        ("Sensitivity CSV", str(Path(sensitivity_csv or DEFAULT_SENSITIVITY_CSV).resolve())),
        ("RGB coefficient before geometry", base_color_coefficient),
        ("Photodiode coefficient (RGB * geometry)", effective_photo),
        ("Integral coefficient (settings)", calibration.integral_coefficient),
        ("Quarter coefficient", calibration.coefficient),
        ("Geometric coefficient", calibration.geometric_coefficient),
        ("Effective coefficient", calibration.effective_coefficient),
        ("Calibration source pixel", calibration.source_pixel),
        ("Calibration source file", calibration.source_file),
        ("Calibration date", calibration.calculated_at),
        ("Points used", calibration.points_used),
        ("R^2", calibration.r_squared),
        (
            "Formula",
            "L_integral = weighted_integral * integral_coefficient * quarter_coefficient * geometric_coefficient",
        ),
    ]
    for row, (label, value) in enumerate(metadata, start=3):
        ws.cell(row, 1, label).font = Font(bold=True)
        ws.cell(row, 2, value)

    headers = [
        "Point",
        "V set (V)",
        "I photodiode (uA)",
        "Luminance by photodiode (cd/m^2)",
        "Shape integral (CIE/BPW34)",
        "Weighted integral (counts/s*nm)",
        "Luminance by integral (cd/m^2)",
        "Deviation (%)",
        "Status",
    ]
    header_row = 3 + len(metadata) + 2
    for column, header in enumerate(headers, start=1):
        ws.cell(header_row, column, header)
    style_header_row(ws, header_row, 1, len(headers))

    for row, point in enumerate(points, start=header_row + 1):
        weighted = as_float_or_none(point.get("weighted_integral"))
        photo_luminance = as_float_or_none(point.get("photodiode_luminance_cd_m2"))
        integral_luminance = integral_luminance_cd_m2(
            weighted,
            calibration.integral_coefficient,
            calibration.geometric_coefficient,
            calibration.coefficient,
        )
        deviation = None
        if (
            photo_luminance is not None
            and photo_luminance != 0
            and integral_luminance is not None
        ):
            deviation = (integral_luminance - photo_luminance) / photo_luminance * 100.0
        values = [
            point.get("point"),
            point.get("voltage_V"),
            point.get("photodiode_uA"),
            photo_luminance,
            point.get("shape_integral"),
            weighted,
            integral_luminance,
            deviation,
            point.get("status"),
        ]
        for column, value in enumerate(values, start=1):
            ws.cell(row, column, value)

    last_row = header_row + len(points)
    if last_row > header_row + 1:
        chart = ScatterChart()
        chart.title = "Светимость и CIE/BPW34-интеграл"
        chart.x_axis.title = "Weighted integral (counts/s*nm)"
        chart.y_axis.title = "Luminance (cd/m^2)"
        chart.x_axis.majorGridlines = ChartLines()
        x_values = Reference(ws, min_col=6, min_row=header_row + 1, max_row=last_row)
        photo_values = Reference(ws, min_col=4, min_row=header_row, max_row=last_row)
        integral_values = Reference(ws, min_col=7, min_row=header_row, max_row=last_row)
        chart.series.append(Series(photo_values, x_values, title_from_data=True))
        chart.series.append(Series(integral_values, x_values, title_from_data=True))
        chart.height = 10
        chart.width = 18
        ws.add_chart(chart, "K3")

    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:I{max(last_row, header_row)}"
    for row in range(header_row + 1, last_row + 1):
        ws.cell(row, 8).number_format = "0.00"
    ws["A1"].fill = PatternFill("solid", fgColor="D9EAF7")
    autosize_columns(ws, max_width=52)

    temp_path = output_path.with_name(f".{output_path.stem}.tmp.xlsx")
    wb.save(temp_path)
    wb.close()
    temp_path.replace(output_path)
    return output_path
