"""Ossila/Xtralien COM-port discovery helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from oled_app.constants import HARDWARE_MODE_REAL
from oled_app.hardware.simulator import uninstall_simulator_modules


def list_serial_ports() -> List[Any]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    try:
        return list(list_ports.comports())
    except Exception:
        return []


def find_ossila_com_port(log: Optional[Callable[[str], None]] = None) -> Optional[str]:
    ports = list_serial_ports()
    if not ports:
        return None

    def port_text(port) -> str:
        return " ".join(
            str(getattr(port, attr, "") or "")
            for attr in ("device", "description", "manufacturer", "product", "hwid")
        ).lower()

    preferred_words = ("ossila", "xtralien")
    preferred = [port for port in ports if any(word in port_text(port) for word in preferred_words)]
    candidates = preferred + [port for port in ports if port not in preferred]

    try:
        uninstall_simulator_modules()
        import xtralien
    except Exception:
        return str(getattr(preferred[0], "device", "")) if preferred else None

    for port in candidates:
        device = str(getattr(port, "device", "") or "")
        if not device:
            continue
        try:
            with xtralien.Device(device) as smu:
                try:
                    smu.smu1.set.voltage(0, response=0)
                    smu.smu2.set.voltage(0, response=0)
                except Exception:
                    pass
            if log:
                log(f"Авто-COM Ossila: найден {device}")
            return device
        except Exception as exc:
            if log:
                log(f"Авто-COM Ossila: {device} не подошел ({exc})")
    return None


def effective_com_port(settings: Dict[str, Any], log: Optional[Callable[[str], None]] = None) -> str:
    if settings.get("hardware_mode") == HARDWARE_MODE_REAL and bool(settings.get("auto_com_port", False)):
        found = find_ossila_com_port(log)
        if found:
            return found
    return str(settings.get("com_port") or "COM3")
