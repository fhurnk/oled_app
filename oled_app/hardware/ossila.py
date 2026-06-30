"""Small Ossila/Xtralien helpers for the modular hardware layer."""

from __future__ import annotations

import time


def safe_shutdown_smu(smu) -> None:
    try:
        smu.smu1.set.voltage(0, response=0)
    except Exception:
        pass
    try:
        smu.smu2.set.voltage(0, response=0)
    except Exception:
        pass
    time.sleep(0.2)
    try:
        smu.smu1.set.enabled(False, response=0)
    except Exception:
        pass
    try:
        smu.smu2.set.enabled(False, response=0)
    except Exception:
        pass
