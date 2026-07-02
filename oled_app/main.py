"""Entrypoint for the modular OLED application."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Optional

from .constants import APP_VERSION, SCRIPT_DIR

REFERENCE_APP = SCRIPT_DIR / "oled_measurement_app_v2_5.py"
MIGRATED_PACKAGE_LAYERS = (
    "constants",
    "settings",
    "utils",
    "series",
    "hardware",
    "reports",
    "measurements.ivl",
    "measurements.raw_io",
    "measurements.spectrum",
    "measurements.stability",
    "processing.ivl_results",
    "processing.spectrum_results",
    "processing.stability_results",
    "gui.widgets",
    "gui.progress",
    "gui.app",
    "gui.start_screen",
    "gui.measurement_menu",
    "gui.settings_window",
    "gui.ivl_window",
    "gui.spectrum_window",
    "gui.stability_window",
    "gui.report_window",
)


def application_status_lines() -> list[str]:
    return [
        f"OLED modular application v{APP_VERSION}",
        f"Reference application: {REFERENCE_APP}",
        "Default launch: modular Tk GUI",
        "Migrated package layers: " + ", ".join(MIGRATED_PACKAGE_LAYERS),
    ]


def scaffold_status_lines() -> list[str]:
    """Compatibility alias for earlier scaffold checks."""
    return application_status_lines()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the modular OLED measurement application.")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print modularization status and exit without opening the GUI.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.status:
        for line in application_status_lines():
            print(line)
        return 0

    from .gui import OLEDModularApp

    app = OLEDModularApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
