#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone entrypoint for the isolated OLED v2 desktop prototype."""

from __future__ import annotations

import traceback

from oled_v2.launcher import main
from oled_v2.logging_setup import log_directory


def _write_unhandled_crash() -> None:
    try:
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "oled-v2-crash.log").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _write_unhandled_crash()
        raise
