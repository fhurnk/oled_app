"""Series filesystem path helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from oled_app.constants import MEASUREMENT_FOLDER_NAMES
from oled_app.series.metadata import quarter_code
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


def ensure_quarter_calibration_folder(
    series_folder: Path,
    config: Dict[str, Any],
    quarter_number: int,
) -> Path:
    """Keep every integral-calibration artifact in its quarter folder."""

    quarter_number = int(quarter_number)
    quarter_name = safe_filename(
        f"{quarter_code(config, quarter_number)}{quarter_number}",
        fallback=f"Q{quarter_number}",
    )
    output_dir = Path(series_folder) / "calibration" / quarter_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def ensure_camera_session_folder(
    series_folder: Path,
    pixel_id: str,
    pixel_row: Optional[Dict[str, Any]] = None,
) -> Path:
    """Create a numbered camera session independent of IVL/stability folders."""

    camera_root = ensure_measurement_folder(
        series_folder,
        "CAMERA",
        pixel_id,
        pixel_row,
    )
    legacy_camera_root = camera_root / "camera"
    numbered_session_roots = [camera_root]
    if legacy_camera_root.is_dir():
        numbered_session_roots.append(legacy_camera_root)

    session_number = max(
        (
            int(item.name)
            for session_root in numbered_session_roots
            for item in session_root.iterdir()
            if item.is_dir() and item.name.isdigit()
        ),
        default=0,
    ) + 1
    while True:
        session_dir = camera_root / str(session_number)
        try:
            session_dir.mkdir()
        except FileExistsError:
            session_number += 1
            continue
        return session_dir
