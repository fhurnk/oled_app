"""Small Ossila/Xtralien helpers for the modular hardware layer."""

from __future__ import annotations

import time
from typing import Callable, Optional


def safe_shutdown_smu(smu) -> bool:
    """Try to bring both SMU channels to a safe state on the current connection."""

    smu1_zero = False
    smu2_zero = False
    smu1_disabled = False
    smu2_disabled = False
    try:
        smu.smu1.set.voltage(0, response=0)
        smu1_zero = True
    except Exception:
        pass
    try:
        smu.smu2.set.voltage(0, response=0)
        smu2_zero = True
    except Exception:
        pass
    time.sleep(0.2)
    try:
        smu.smu1.set.enabled(False, response=0)
        smu1_disabled = True
    except Exception:
        pass
    try:
        smu.smu2.set.enabled(False, response=0)
        smu2_disabled = True
    except Exception:
        pass
    return (smu1_zero or smu1_disabled) and (smu2_zero or smu2_disabled)


def shutdown_smu_with_reconnect(
    smu,
    com_port: str,
    log: Optional[Callable[[str], None]] = None,
    attempts: int = 4,
    retry_delay_s: float = 0.5,
    device_factory=None,
) -> bool:
    """Shut down an SMU, reopening its serial port if the active connection has failed."""

    report = log or (lambda _message: None)
    if safe_shutdown_smu(smu):
        return True

    report("Аварийное выключение через текущее соединение SMU не удалось; переподключение к прибору.")
    try:
        smu.close()
    except Exception:
        pass

    if device_factory is None:
        import xtralien

        device_factory = xtralien.Device

    for attempt in range(1, max(1, int(attempts)) + 1):
        time.sleep(max(0.0, float(retry_delay_s)))
        recovery_smu = None
        try:
            recovery_smu = device_factory(com_port)
            if safe_shutdown_smu(recovery_smu):
                report(f"Выходы SMU сброшены после переподключения (попытка {attempt}).")
                return True
            report(f"SMU переподключён, но команды аварийного сброса не приняты (попытка {attempt}).")
        except Exception as exc:
            report(f"Попытка аварийного переподключения SMU {attempt} не удалась: {exc}")
        finally:
            if recovery_smu is not None:
                try:
                    recovery_smu.close()
                except Exception:
                    pass

    report(
        "КРИТИЧЕСКИ: приложение не смогло подтвердить отключение выходов SMU. "
        "Немедленно отключите выход прибора вручную."
    )
    return False
