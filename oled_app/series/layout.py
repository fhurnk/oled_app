"""Substrate holder layout helpers for series GUI maps."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


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
