"""Series filesystem path helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from oled_app.constants import MEASUREMENT_FOLDER_NAMES
from oled_app.utils import safe_filename, today_iso


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
