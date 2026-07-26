"""Small value-free IVL thumbnails for hover previews on the pixel map."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from openpyxl import load_workbook
from PIL import Image, ImageDraw

from oled_app.utils import as_float_or_none


THUMBNAIL_SUFFIX = "_thumbnail.png"


def ivl_thumbnail_path(workbook_path: Path) -> Path:
    workbook_path = Path(workbook_path)
    return workbook_path.with_name(f"{workbook_path.stem}{THUMBNAIL_SUFFIX}")


def _series_points(
    voltages: Sequence[float],
    values: Sequence[float],
    left: int,
    top: int,
    width: int,
    height: int,
    logarithmic: bool,
) -> List[tuple[int, int]]:
    if len(voltages) < 2 or len(values) != len(voltages):
        return []
    x_min, x_max = min(voltages), max(voltages)
    if math.isclose(x_min, x_max):
        return []
    if logarithmic:
        positive = [abs(value) for value in values if value and math.isfinite(value)]
        floor = max(min(positive, default=1e-9) * 0.1, 1e-12)
        transformed = [math.log10(max(abs(value), floor)) for value in values]
    else:
        transformed = list(values)
    y_min, y_max = min(transformed), max(transformed)
    if math.isclose(y_min, y_max):
        y_max = y_min + 1.0
    return [
        (
            int(left + (voltage - x_min) / (x_max - x_min) * width),
            int(top + height - (value - y_min) / (y_max - y_min) * height),
        )
        for voltage, value in zip(voltages, transformed)
    ]


def create_ivl_thumbnail(
    output_path: Path,
    cycles: Iterable[Dict[str, Any]],
    size: tuple[int, int] = (340, 220),
) -> Path:
    """Draw current and photodiode curves without numeric axes or values."""

    width, height = size
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = 28, 18, width - 18, height - 24
    draw.rectangle((left, top, right, bottom), outline="#B7C2CC", width=1)
    colors = ("#C43C30", "#2F80ED", "#7A4FB3", "#2FA66A")

    any_points = False
    for cycle_index, cycle in enumerate(cycles):
        rows = list(cycle.get("data") or [])
        voltages = [
            as_float_or_none(
                row.get("Voltage OLED / LED measured (V)", row.get("Voltage set (V)"))
            )
            for row in rows
        ]
        currents = [as_float_or_none(row.get("Current OLED / LED (mA)")) for row in rows]
        photo = [as_float_or_none(row.get("Photodiode current (uA)")) for row in rows]
        valid = [
            (float(v), float(i), float(p))
            for v, i, p in zip(voltages, currents, photo)
            if v is not None and i is not None and p is not None
        ]
        if len(valid) < 2:
            continue
        any_points = True
        v_values = [item[0] for item in valid]
        i_values = [item[1] for item in valid]
        p_values = [item[2] for item in valid]
        current_points = _series_points(
            v_values,
            i_values,
            left,
            top,
            right - left,
            bottom - top,
            logarithmic=True,
        )
        photo_points = _series_points(
            v_values,
            p_values,
            left,
            top,
            right - left,
            bottom - top,
            logarithmic=False,
        )
        if current_points:
            draw.line(current_points, fill=colors[cycle_index % len(colors)], width=3)
        if photo_points:
            draw.line(photo_points, fill="#2F80ED", width=2)

    if any_points:
        draw.line((left + 8, top + 10, left + 34, top + 10), fill="#C43C30", width=3)
        draw.text((left + 40, top + 4), "I OLED", fill="#45515C")
        draw.line((left + 100, top + 10, left + 126, top + 10), fill="#2F80ED", width=2)
        draw.text((left + 132, top + 4), "PD", fill="#45515C")
    else:
        draw.text((width // 2 - 42, height // 2 - 8), "Нет данных ВАЯХ", fill="#6B7280")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.stem}.tmp.png")
    image.save(temp_path, format="PNG", optimize=True)
    temp_path.replace(output_path)
    return output_path


def create_ivl_thumbnail_from_workbook(workbook_path: Path, output_path: Path | None = None) -> Path:
    workbook_path = Path(workbook_path)
    cycles: List[Dict[str, Any]] = []
    wb = load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        for sheet_name in wb.sheetnames:
            if not sheet_name.startswith("Cycle_"):
                continue
            ws = wb[sheet_name]
            header_row = None
            headers: Dict[str, int] = {}
            for row in range(1, min(ws.max_row, 20) + 1):
                candidate = {
                    str(ws.cell(row, column).value or ""): column
                    for column in range(1, ws.max_column + 1)
                }
                if "Current OLED / LED (mA)" in candidate and "Photodiode current (uA)" in candidate:
                    header_row = row
                    headers = candidate
                    break
            if header_row is None:
                continue
            data = []
            for row in range(header_row + 1, ws.max_row + 1):
                point = {
                    "Voltage OLED / LED measured (V)": ws.cell(
                        row,
                        headers.get("Voltage OLED / LED measured (V)", headers.get("Voltage set (V)", 2)),
                    ).value,
                    "Current OLED / LED (mA)": ws.cell(
                        row,
                        headers["Current OLED / LED (mA)"],
                    ).value,
                    "Photodiode current (uA)": ws.cell(
                        row,
                        headers["Photodiode current (uA)"],
                    ).value,
                }
                if any(value not in (None, "") for value in point.values()):
                    data.append(point)
            cycles.append({"data": data})
    finally:
        wb.close()
    return create_ivl_thumbnail(output_path or ivl_thumbnail_path(workbook_path), cycles)
