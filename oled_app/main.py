"""Entrypoint for the new modular OLED application scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from .constants import APP_VERSION, SCRIPT_DIR

REFERENCE_APP = SCRIPT_DIR / "oled_measurement_app_v2_5.py"


def scaffold_status_lines() -> list[str]:
    return [
        f"OLED modular application scaffold v{APP_VERSION}",
        f"Reference application: {REFERENCE_APP}",
        "Migrated package layers: constants, settings, utils, series, hardware, reports, measurements.ivl, measurements.spectrum, measurements.stability",
    ]


def main(argv: Optional[Iterable[str]] = None) -> int:
    for line in scaffold_status_lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
