"""Pixel status formatting for series views and journals."""

from __future__ import annotations


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
