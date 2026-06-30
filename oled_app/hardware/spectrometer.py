"""Spectrometer discovery helpers for the modular hardware layer."""

from __future__ import annotations

from typing import Any, List


def list_spectrometer_devices() -> List[Any]:
    try:
        import seabreeze.spectrometers as sb
    except Exception:
        return []
    try:
        return list(sb.list_devices())
    except Exception:
        return []
