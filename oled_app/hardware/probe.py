"""Hardware status probing for real and simulator modes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from oled_app.constants import HARDWARE_MODE_SIM, SCRIPT_DIR, SIM_CONFIG_FILE
from oled_app.hardware.auto_com import effective_com_port
from oled_app.hardware.simulator import uninstall_simulator_modules
from oled_app.settings import ensure_default_sim_config


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

    try:
        import seabreeze.spectrometers as sb
        devices = list(sb.list_devices())
        if devices:
            result["spectrometer"] = f"Спектрометр OK ({len(devices)})"
        else:
            if result["level"] != "error":
                result["level"] = "warning"
                result["title"] = "SMU готов, спектрометр не найден"
            result["spectrometer"] = "Спектрометр не найден"
    except Exception:
        if result["level"] != "error":
            result["level"] = "warning"
            result["title"] = "SMU готов, ошибка спектрометра"
        result["spectrometer"] = "seabreeze ERROR"

    result["details"] = "; ".join(messages) if messages else f"{result['smu']}; {result['spectrometer']}"
    return result
