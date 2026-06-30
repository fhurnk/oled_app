"""General-purpose helpers shared across the OLED application."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .constants import MEASUREMENT_FOLDER_NAMES


def now_str() -> str:
    return datetime.now().strftime("%d-%m-%Y %H:%M:%S")


def timestamp_for_file() -> str:
    return datetime.now().strftime("%d-%m-%Y_%Hh%Mm%Ss")


def today_iso() -> str:
    return date.today().isoformat()


def safe_filename(text: str, fallback: str = "item") -> str:
    text = (text or "").strip()
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.-")
    return text or fallback


def as_float_or_none(value) -> Optional[float]:
    if value in (None, "", "—"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def current_density_mA_cm2(current_mA: Any, pixel_area_mm2: Any) -> Optional[float]:
    current = as_float_or_none(current_mA)
    area = as_float_or_none(pixel_area_mm2)
    if current is None or area is None or area <= 0:
        return None
    return float(current) / (float(area) / 100.0)


def luminance_cd_m2(photo_uA: Any, conversion_cd_m2_per_uA: Any) -> Optional[float]:
    photo = as_float_or_none(photo_uA)
    coeff = as_float_or_none(conversion_cd_m2_per_uA)
    if photo is None or coeff is None:
        return None
    return float(photo) * float(coeff)


def relative_to_or_abs(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path.resolve())


def resolve_series_file(series_folder: Path, file_value: Any) -> Optional[Path]:
    if not file_value:
        return None
    path = Path(str(file_value))
    if not path.is_absolute():
        path = Path(series_folder) / path
    return path if path.exists() else None


def read_spectrum_metrics_from_workbook(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
        ws = wb.active
        header_row = 14
        headers = {}
        for candidate in range(1, min(ws.max_row, 40) + 1):
            row_headers = {str(ws.cell(candidate, c).value or ""): c for c in range(1, ws.max_column + 1)}
            if "Peaks detected" in row_headers or "Max intensity processed (counts)" in row_headers or "Max intensity processed (counts/s)" in row_headers or "Max intensity (counts)" in row_headers:
                header_row = candidate
                headers = row_headers
                break
        peaks_col = headers.get("Peaks detected")
        peaks_nm_col = headers.get("Peaks nm")
        max_int_col = headers.get("Max intensity processed (counts)") or headers.get("Max intensity processed (counts/s)") or headers.get("Max intensity (counts)")
        best = {"peak_count": "", "peaks_nm": "", "max_intensity": ""}
        best_intensity = -1.0
        for row in range(header_row + 1, ws.max_row + 1):
            intensity = as_float_or_none(ws.cell(row, max_int_col).value) if max_int_col else None
            if intensity is None:
                continue
            if intensity >= best_intensity:
                best_intensity = float(intensity)
                best = {
                    "peak_count": ws.cell(row, peaks_col).value if peaks_col else "",
                    "peaks_nm": ws.cell(row, peaks_nm_col).value if peaks_nm_col else "",
                    "max_intensity": round(float(intensity), 1),
                }
        wb.close()
        return best
    except Exception:
        return {}


def ensure_day_folder(series_folder: Path) -> Path:
    day_folder = series_folder / "measurements" / today_iso()
    day_folder.mkdir(parents=True, exist_ok=True)
    return day_folder


def ensure_measurement_folder(
    series_folder: Path,
    measurement_type: str,
    pixel_id: str,
    pixel_row: Optional[Dict[str, Any]] = None,
) -> Path:
    measurement_folder = MEASUREMENT_FOLDER_NAMES.get(
        str(measurement_type).upper(),
        safe_filename(str(measurement_type), fallback="measurement"),
    )
    pixel_row = pixel_row or {}

    quarter_number = pixel_row.get("Quarter number") or "unknown"
    quarter_code = pixel_row.get("Quarter code") or "Q"
    substrate_number = pixel_row.get("Substrate number") or "unknown"

    quarter_name = safe_filename(f"{quarter_code}{quarter_number}", fallback=f"Q{quarter_number}")
    substrate_folder = safe_filename(f"{quarter_name}_{substrate_number}", fallback=f"{quarter_name}_unknown")
    pixel_folder = safe_filename(pixel_id, fallback="pixel")

    output_dir = (
        series_folder
        / "measurements"
        / measurement_folder
        / today_iso()
        / quarter_name
        / substrate_folder
        / pixel_folder
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def pixel_status_color(status: str) -> str:
    status = str(status or "").upper()
    if status == "WORKING":
        return "#8FD694"
    if status == "NO_CONTACT":
        return "#F2D96B"
    if status == "NEEDS_REVIEW":
        return "#F4A261"
    if status in {"NONWORKING", "BURNED", "FAILED", "CURRENT_LIMIT_STOP", "CURRENT_LIMIT"}:
        return "#F28B82"
    return "#D9D9D9"


def ivl_status_marker(status: str) -> str:
    status = str(status or "").upper()
    if status == "WORKING":
        return "↑ WORKING"
    if status == "NO_CONTACT":
        return "→ NO_CONTACT"
    if status == "NEEDS_REVIEW":
        return "? NEEDS_REVIEW"
    if status in {"NONWORKING", "FAILED"}:
        return "↓ " + status
    if status in {"BURNED", "CURRENT_LIMIT_STOP", "CURRENT_LIMIT"}:
        return "↯ " + status
    return "· " + (status or "")


def build_holder_layout(width: int = 930, height: int = 620) -> Dict[int, Dict[str, Any]]:
    """Geometry for substrate holder maps in the legacy GUI."""
    box_w = 86
    box_h = 52

    left_x1, left_x2, left_x3 = 170, 305, 238
    right_x1, right_x2, right_x3 = width - 390, width - 255, width - 322
    top_y1, top_y3 = 145, 235
    bottom_y1, bottom_y3 = 320, 405

    quarter_layout = {
        2: {
            "number_xy": (48, 92),
            "name_xy": (92, 36),
            "entry_xy": (78, 62),
            "substrates": [(left_x1, top_y1), (left_x2, top_y1), (left_x3, top_y3)],
        },
        1: {
            "number_xy": (width - 48, 92),
            "name_xy": (width - 248, 36),
            "entry_xy": (width - 230, 62),
            "substrates": [(right_x1, top_y1), (right_x2, top_y1), (right_x3, top_y3)],
        },
        3: {
            "number_xy": (48, height - 118),
            "name_xy": (92, height - 155),
            "entry_xy": (78, height - 130),
            "substrates": [(left_x1, bottom_y1), (left_x2, bottom_y1), (left_x3, bottom_y3)],
        },
        4: {
            "number_xy": (width - 48, height - 118),
            "name_xy": (width - 248, height - 155),
            "entry_xy": (width - 230, height - 130),
            "substrates": [(right_x1, bottom_y1), (right_x2, bottom_y1), (right_x3, bottom_y3)],
        },
    }

    for info in quarter_layout.values():
        detailed = []
        for substrate_number, (x, y) in enumerate(info["substrates"], start=1):
            detailed.append({"substrate_number": substrate_number, "x": x, "y": y, "w": box_w, "h": box_h})
        info["substrates"] = detailed
    return quarter_layout


def short_date_for_map(value: str) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%y")
    except Exception:
        return text


def light_border() -> Border:
    side = Side(style="thin", color="D9D9D9")
    return Border(left=side, right=side, top=side, bottom=side)


def style_header_row(ws, row: int, min_col: int, max_col: int):
    fill = PatternFill("solid", fgColor="D9E1F2")
    font = Font(bold=True)
    border = light_border()
    for col in range(min_col, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border


def autosize_columns(ws, min_width: int = 10, max_width: int = 42):
    for col_cells in ws.columns:
        max_len = 0
        letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            value = cell.value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[letter].width = max(min_width, min(max_width, max_len + 2))


def parse_float(text: str, field_name: str) -> float:
    try:
        return float(str(text).replace(",", "."))
    except Exception:
        raise ValueError(f"Поле '{field_name}' должно быть числом")


def parse_int(text: str, field_name: str) -> int:
    try:
        return int(float(str(text).replace(",", ".")))
    except Exception:
        raise ValueError(f"Поле '{field_name}' должно быть целым числом")


def build_report_voltage_grid(start: float, stop: float, step: float) -> List[float]:
    if step <= 0:
        raise ValueError("Шаг напряжения должен быть положительным")
    if stop < start:
        raise ValueError("Конец диапазона должен быть не меньше начала")
    values: List[float] = []
    current = float(start)
    while current <= stop + step / 2:
        values.append(round(current, 6))
        current += step
        if len(values) > 10000:
            raise ValueError("Слишком большая сетка напряжений для отчета")
    return values


def voltage_grid_missing(requested: Iterable[float], available: Iterable[float]) -> List[float]:
    available_set = {round(float(value), 6) for value in available}
    return [value for value in requested if round(float(value), 6) not in available_set]


def format_voltage(value: float) -> str:
    return f"{float(value):g}"
