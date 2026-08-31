"""Substrate holder layout helpers for series GUI maps."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .metadata import normalize_quarter_layout


def build_holder_layout(
    width: int = 930,
    height: int = 620,
    quarter_layout: Any = None,
) -> Dict[int, Dict[str, Any]]:
    """Geometry for substrate holder maps in the legacy GUI."""
    box_w = 86
    box_h = 52

    left_outer_x, left_inner_x = 170, 305
    right_inner_x, right_outer_x = width - 390, width - 255
    top_y1, top_y3 = 145, 235
    bottom_y1, bottom_y3 = 320, 405

    position_layout = {
        "top_left": {
            "number_xy": (48, 92),
            "name_xy": (92, 36),
            "entry_xy": (78, 62),
            "substrates": [(left_inner_x, top_y1), (left_inner_x, top_y3), (left_outer_x, top_y3)],
        },
        "top_right": {
            "number_xy": (width - 48, 92),
            "name_xy": (width - 248, 36),
            "entry_xy": (width - 230, 62),
            "substrates": [(right_inner_x, top_y1), (right_inner_x, top_y3), (right_outer_x, top_y3)],
        },
        "bottom_left": {
            "number_xy": (48, height - 118),
            "name_xy": (92, height - 155),
            "entry_xy": (78, height - 130),
            "substrates": [(left_inner_x, bottom_y3), (left_inner_x, bottom_y1), (left_outer_x, bottom_y1)],
        },
        "bottom_right": {
            "number_xy": (width - 48, height - 118),
            "name_xy": (width - 248, height - 155),
            "entry_xy": (width - 230, height - 130),
            "substrates": [(right_inner_x, bottom_y3), (right_inner_x, bottom_y1), (right_outer_x, bottom_y1)],
        },
    }

    normalized = normalize_quarter_layout(quarter_layout)
    result = {
        normalized[position]: info
        for position, info in position_layout.items()
    }
    for info in result.values():
        detailed = []
        for substrate_number, (x, y) in enumerate(info["substrates"], start=1):
            detailed.append({"substrate_number": substrate_number, "x": x, "y": y, "w": box_w, "h": box_h})
        info["substrates"] = detailed
    return result


def short_date_for_map(value: str) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%y")
    except Exception:
        return text
