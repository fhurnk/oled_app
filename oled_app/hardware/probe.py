"""Hardware status probing for real and simulator modes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from oled_app.constants import HARDWARE_MODE_SIM, SCRIPT_DIR, SIM_CONFIG_FILE
from oled_app.hardware.auto_com import effective_com_port
from oled_app.hardware.simulator import uninstall_simulator_modules
from oled_app.settings import ensure_default_sim_config


SPECTROMETER_PROBE_TIMEOUT_S = 5.0


def probe_spectrometer(timeout_s: float = SPECTROMETER_PROBE_TIMEOUT_S) -> Tuple[str, int]:
    """Discover SeaBreeze devices without letting its native driver freeze the GUI.

    Some SeaBreeze/USB combinations can block inside ``list_devices`` while holding
    the Python GIL. A thread therefore is not sufficient isolation. The short-lived
    child process can be terminated safely when discovery exceeds the timeout.
    """

    script = (
        "import seabreeze\n"
        "seabreeze.use('cseabreeze')\n"
        "from seabreeze.spectrometers import list_devices\n"
        "print('OLED_SPEC_COUNT=' + str(len(list_devices())))\n"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=max(float(timeout_s), 0.1),
            check=False,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired:
        return "timeout", 0
    except Exception:
        return "error", 0

    if completed.returncode != 0:
        return "error", 0
    try:
        count_line = next(
            line for line in reversed(completed.stdout.splitlines()) if line.startswith("OLED_SPEC_COUNT=")
        )
        count = int(count_line.partition("=")[2])
    except (StopIteration, TypeError, ValueError):
        return "error", 0
    return ("ok", count) if count > 0 else ("not_found", 0)


def probe_hardware(settings: Dict[str, Any]) -> Dict[str, Any]:
    mode = settings.get("hardware_mode", HARDWARE_MODE_SIM)
    if mode == HARDWARE_MODE_SIM:
        cfg_path = Path(settings.get("simulator_config_path") or SCRIPT_DIR / SIM_CONFIG_FILE)
        ensure_default_sim_config(cfg_path)
        return {
            "level": "ok",
            "title": "Эмулятор готов",
            "details": f"SIM, конфиг: {cfg_path.name}",
            "smu": "SIM OK",
            "spectrometer": "SIM OK",
        }

    result = {
        "level": "ok",
        "title": "Оборудование готово",
        "details": "",
        "smu": "",
        "spectrometer": "",
    }
    messages: List[str] = []
    uninstall_simulator_modules()
    com_port = effective_com_port(settings)
    if settings.get("auto_com_port") and com_port != str(settings.get("com_port") or ""):
        result["auto_com_port"] = com_port

    try:
        import xtralien
        with xtralien.Device(com_port) as smu:
            try:
                smu.smu1.set.voltage(0, response=0)
                smu.smu2.set.voltage(0, response=0)
            except Exception:
                pass
        result["smu"] = f"xtralien OK ({com_port})"
    except Exception:
        result["level"] = "error"
        result["title"] = "SMU не отвечает"
        result["smu"] = f"xtralien ERROR ({com_port})"

    spectrometer_status, device_count = probe_spectrometer()
    if spectrometer_status == "ok":
        result["spectrometer"] = f"Спектрометр OK ({device_count})"
    elif spectrometer_status == "not_found":
        if result["level"] != "error":
            result["level"] = "warning"
            result["title"] = "SMU готов, спектрометр не найден"
        result["spectrometer"] = "Спектрометр не найден"
    elif spectrometer_status == "timeout":
        if result["level"] != "error":
            result["level"] = "warning"
            result["title"] = "SMU готов, спектрометр не отвечает"
        result["spectrometer"] = f"Спектрометр: тайм-аут {SPECTROMETER_PROBE_TIMEOUT_S:g} с"
    else:
        if result["level"] != "error":
            result["level"] = "warning"
            result["title"] = "SMU готов, ошибка спектрометра"
        result["spectrometer"] = "seabreeze ERROR"

    result["details"] = "; ".join(messages) if messages else f"{result['smu']}; {result['spectrometer']}"
    return result
