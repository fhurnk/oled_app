"""Built-in xtralien and seabreeze simulator for the modular scaffold."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
import types
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from oled_app.constants import HARDWARE_MODE_SIM, SCRIPT_DIR, SIM_CONFIG_FILE
from oled_app.settings import (
    DEFAULT_SIMULATOR_CONFIG,
    deep_update,
    ensure_default_sim_config,
    load_app_settings,
)

_SIM_STATE: Dict[str, Any] = {
    "pixel_id": "SIM_PIXEL",
    "session_start_monotonic": time.monotonic(),
    "smu1_voltage_set_V": 0.0,
    "smu2_voltage_set_V": 0.0,
    "smu1_enabled": False,
    "smu2_enabled": False,
    "led_current_mA": 0.0,
    "photodiode_uA": 0.0,
    "latched_burned": False,
    "rng": random.Random(42),
}


def sim_load_config(config_path: Optional[Path]) -> Dict[str, Any]:
    cfg = deepcopy(DEFAULT_SIMULATOR_CONFIG)
    path = Path(config_path) if config_path else SCRIPT_DIR / SIM_CONFIG_FILE
    if path.exists():
        try:
            cfg = deep_update(cfg, json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def sim_global_settings() -> Dict[str, Any]:
    return sim_load_config(Path(os.environ.get("OLED_SIM_CONFIG", SCRIPT_DIR / SIM_CONFIG_FILE))).get("global", {})


def sim_current_pixel_id() -> str:
    return os.environ.get("OLED_SIM_PIXEL_ID", "SIM_PIXEL")


def sim_profile_for_pixel(pixel_id: Optional[str] = None) -> Dict[str, Any]:
    cfg = sim_load_config(Path(os.environ.get("OLED_SIM_CONFIG", SCRIPT_DIR / SIM_CONFIG_FILE)))
    pixel_id = pixel_id or sim_current_pixel_id()
    profile = deepcopy(cfg.get("default_pixel", {}))
    pixels_cfg = cfg.get("pixels", {})
    explicit = pixels_cfg.get(pixel_id)
    if explicit is None:
        match = re.search(r"(\d+)_(\d+)_(\d+)$", str(pixel_id))
        if match:
            explicit = pixels_cfg.get(f"Q{match.group(1)}_{match.group(2)}_{match.group(3)}")
    if explicit is None:
        explicit = sim_generated_profile_for_pixel(pixel_id)
    profile = deep_update(profile, explicit)
    mode = str(profile.get("mode", "working")).lower()
    if mode == "weak" and "photodiode_gain_uA_per_mA" not in profile:
        profile["photodiode_gain_uA_per_mA"] = 0.05
    return profile


def sim_generated_profile_for_pixel(pixel_id: str) -> Dict[str, Any]:
    match = re.search(r"(\d+)_(\d+)_(\d+)$", str(pixel_id))
    if not match:
        return {}
    q, substrate, pix = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    selector = (q * 17 + substrate * 5 + pix * 3) % 10
    if selector == 0:
        return {"mode": "no_contact", "leakage_mA": 0.001}
    if selector in {1, 2}:
        return {
            "mode": "nonworking",
            "turn_on_voltage_V": 2.8 + 0.05 * q,
            "current_at_5V_mA": 2.0 + 0.3 * substrate,
            "photodiode_gain_uA_per_mA": 0.0,
        }
    if selector == 3:
        return {
            "mode": "weak",
            "turn_on_voltage_V": 2.55 + 0.04 * q,
            "current_at_5V_mA": 4.0 + 0.2 * pix,
            "photodiode_gain_uA_per_mA": 0.04,
            "spectrum": {"counts_per_mA_per_s": 120_000.0},
        }
    if selector == 4:
        return {
            "mode": "working",
            "turn_on_voltage_V": 2.45 + 0.06 * q,
            "current_at_5V_mA": 5.0 + 0.4 * substrate,
            "burnout_voltage_V": 3.4 + 0.1 * pix,
            "short_resistance_ohm": 90.0,
        }
    return {
        "mode": "working",
        "turn_on_voltage_V": 2.45 + 0.08 * q + 0.02 * pix,
        "current_at_5V_mA": 4.5 + 0.6 * substrate + 0.25 * pix,
        "photodiode_gain_uA_per_mA": 0.35 + 0.05 * ((q + substrate + pix) % 5),
    }


def sim_start_session(pixel_id: Optional[str] = None) -> None:
    pixel_id = pixel_id or sim_current_pixel_id()
    seed_base = int(sim_global_settings().get("random_seed", 42))
    seed = seed_base + sum(ord(ch) for ch in pixel_id)
    _SIM_STATE.update({
        "pixel_id": pixel_id,
        "session_start_monotonic": time.monotonic(),
        "smu1_voltage_set_V": 0.0,
        "smu2_voltage_set_V": 0.0,
        "smu1_enabled": False,
        "smu2_enabled": False,
        "led_current_mA": 0.0,
        "photodiode_uA": 0.0,
        "latched_burned": False,
        "rng": random.Random(seed),
    })


def sim_elapsed_s() -> float:
    return max(0.0, time.monotonic() - float(_SIM_STATE.get("session_start_monotonic", time.monotonic())))


def sim_noise(value: float, relative: float = 0.0, absolute: float = 0.0) -> float:
    rng: random.Random = _SIM_STATE.get("rng") or random.Random(42)
    sigma = abs(value) * float(relative) + float(absolute)
    if sigma <= 0:
        return value
    return value + rng.gauss(0.0, sigma)


def sim_is_no_light_mode(mode: str) -> bool:
    return mode in {"nonworking", "no_light", "dead", "burned", "short", "no_contact", "open"}


def sim_set_channel_voltage(channel: int, voltage: float) -> None:
    _SIM_STATE[f"smu{channel}_voltage_set_V"] = float(voltage)


def sim_set_channel_enabled(channel: int, enabled: bool) -> None:
    _SIM_STATE[f"smu{channel}_enabled"] = bool(enabled)


def sim_set_channel_range(channel: int, range_index: int) -> None:
    _SIM_STATE[f"smu{channel}_range"] = int(range_index)


def sim_led_current_mA(voltage_V: Optional[float] = None) -> float:
    p = sim_profile_for_pixel(_SIM_STATE.get("pixel_id"))
    mode = str(p.get("mode", "working")).lower()
    g = sim_global_settings()
    t = sim_elapsed_s()
    voltage = float(_SIM_STATE.get("smu1_voltage_set_V", 0.0) if voltage_V is None else voltage_V)

    if not _SIM_STATE.get("smu1_enabled", False):
        current = 0.0
    elif mode in {"no_contact", "open"}:
        current = float(p.get("leakage_mA", 0.0005))
    else:
        burnout_after_s = p.get("burnout_after_s")
        if burnout_after_s is not None and t >= float(burnout_after_s):
            _SIM_STATE["latched_burned"] = True
        burnout_voltage = p.get("burnout_voltage_V")
        if burnout_voltage is not None and voltage >= float(burnout_voltage):
            _SIM_STATE["latched_burned"] = True
        if mode in {"burned", "short"} or _SIM_STATE.get("latched_burned", False):
            r_short = max(float(p.get("short_resistance_ohm", 120.0)), 1e-9)
            current = max(0.0, voltage) * 1000.0 / r_short
        else:
            v_turn = float(p.get("turn_on_voltage_V", 2.65)) + float(p.get("voltage_drift_V_per_s", 0.0)) * t
            leakage = float(p.get("leakage_mA", 0.0005))
            if voltage <= v_turn:
                current = leakage * max(voltage / max(v_turn, 1e-9), 0.0)
            else:
                denom = max(5.0 - v_turn, 0.1)
                ratio = max((voltage - v_turn) / denom, 0.0)
                exponent = max(float(p.get("iv_exponent", 2.0)), 0.1)
                current = float(p.get("current_at_5V_mA", 6.0)) * (ratio ** exponent) + leakage
                current = min(current, float(p.get("max_current_mA", 30.0)))
    current = max(0.0, sim_noise(current, relative=float(g.get("current_noise_relative", 0.0))))
    _SIM_STATE["led_current_mA"] = current
    return current


def sim_photodiode_current_uA() -> float:
    p = sim_profile_for_pixel(_SIM_STATE.get("pixel_id"))
    mode = str(p.get("mode", "working")).lower()
    g = sim_global_settings()
    current_mA = sim_led_current_mA()
    if not _SIM_STATE.get("smu2_enabled", False):
        photo = 0.0
    elif sim_is_no_light_mode(mode) or _SIM_STATE.get("latched_burned", False):
        photo = 0.0
    else:
        gain = float(p.get("photodiode_gain_uA_per_mA", 0.55))
        tau = p.get("degradation_tau_s")
        degradation = 1.0 if tau in (None, 0, "") else math.exp(-sim_elapsed_s() / max(float(tau), 1e-9))
        photo = current_mA * gain * degradation
    photo = max(0.0, sim_noise(photo, absolute=float(g.get("photodiode_noise_uA", 0.0))))
    _SIM_STATE["photodiode_uA"] = photo
    return photo


def sim_measured_voltage(channel: int) -> float:
    g = sim_global_settings()
    set_v = float(_SIM_STATE.get(f"smu{channel}_voltage_set_V", 0.0))
    return sim_noise(set_v, absolute=float(g.get("voltage_noise_V", 0.0)))


def sim_spectrum_counts(wavelengths_nm: np.ndarray, integration_time_s: float) -> np.ndarray:
    p = sim_profile_for_pixel(_SIM_STATE.get("pixel_id"))
    g = sim_global_settings()
    sp = p.get("spectrum", {})
    mode = str(p.get("mode", "working")).lower()
    t_int = max(float(integration_time_s), 1e-9)
    dark = float(sp.get("dark_offset_counts", 120.0)) + float(sp.get("dark_counts_per_s", 250.0)) * t_int
    background = float(sp.get("background_counts_per_s", 50.0)) * t_int
    signal = np.zeros_like(wavelengths_nm, dtype=np.float64)
    current_mA = float(_SIM_STATE.get("led_current_mA", 0.0)) or sim_led_current_mA()
    if not sim_is_no_light_mode(mode) and not _SIM_STATE.get("latched_burned", False):
        tau = p.get("degradation_tau_s")
        degradation = 1.0 if tau in (None, 0, "") else math.exp(-sim_elapsed_s() / max(float(tau), 1e-9))
        scale = current_mA * float(sp.get("counts_per_mA_per_s", 1_200_000.0)) * t_int * degradation
        for peak in sp.get("peaks", []):
            center = float(peak.get("center_nm", 530.0))
            fwhm = max(float(peak.get("fwhm_nm", 70.0)), 1e-9)
            amplitude = float(peak.get("amplitude", 1.0))
            sigma = fwhm / 2.354820045
            signal += amplitude * np.exp(-0.5 * ((wavelengths_nm - center) / sigma) ** 2)
        signal *= scale
    counts = dark + background + signal
    rel_noise = float(g.get("spectrum_noise_relative", 0.0))
    if rel_noise > 0:
        rng: random.Random = _SIM_STATE.get("rng") or random.Random(42)
        noise = np.array([rng.gauss(0.0, max(math.sqrt(max(v, 1.0)), rel_noise * max(v, 1.0))) for v in counts])
        counts = counts + noise
    saturation = float(sp.get("saturation_counts", 65535.0))
    return np.clip(counts, 0.0, saturation)


class _SimSetter:
    def __init__(self, channel: "_SimSMUChannel"):
        self.channel = channel

    def enabled(self, value, response=0):
        sim_set_channel_enabled(self.channel.index, bool(value))
        return None

    def voltage(self, value, response=0):
        sim_set_channel_voltage(self.channel.index, float(value))
        return None

    def range(self, value, response=0):
        sim_set_channel_range(self.channel.index, int(value))
        return None


class _SimSMUChannel:
    def __init__(self, index: int):
        self.index = index
        self.set = _SimSetter(self)

    def measure(self):
        if self.index == 1:
            return [(sim_measured_voltage(1), sim_led_current_mA() / 1000.0)]
        return [(sim_measured_voltage(2), -sim_photodiode_current_uA() / 1_000_000.0)]


class _SimDevice:
    def __init__(self, com_port="SIM", *args, **kwargs):
        self.com_port = com_port
        self.smu1 = _SimSMUChannel(1)
        self.smu2 = _SimSMUChannel(2)

    def __enter__(self):
        sim_start_session(os.environ.get("OLED_SIM_PIXEL_ID", "SIM_PIXEL"))
        return self

    def __exit__(self, exc_type, exc, tb):
        sim_set_channel_voltage(1, 0.0)
        sim_set_channel_voltage(2, 0.0)
        sim_set_channel_enabled(1, False)
        sim_set_channel_enabled(2, False)
        return False


class _SimulatedSpectrometerDevice:
    def __repr__(self):
        return "OLED simulated spectrometer"


class _SimSpectrometer:
    model = "OLED-SIM-SPECTROMETER"

    def __init__(self, device):
        self.device = device
        self._integration_time_s = 0.01
        self._wavelengths = np.arange(350.0, 851.0, 1.0, dtype=np.float64)

    def wavelengths(self):
        return self._wavelengths.copy()

    def integration_time_micros(self, value):
        self._integration_time_s = max(float(value) / 1_000_000.0, 1e-6)
        return None

    def intensities(self):
        return sim_spectrum_counts(self._wavelengths, self._integration_time_s).astype(np.float64)


def install_simulator_modules(pixel_id: str, config_path: Optional[Path]) -> None:
    os.environ["OLED_SIM_PIXEL_ID"] = str(pixel_id)
    if config_path:
        os.environ["OLED_SIM_CONFIG"] = str(config_path)

    xtralien_mod = types.ModuleType("xtralien")
    xtralien_mod.__oled_app_simulator__ = True
    xtralien_mod.Device = _SimDevice

    seabreeze_mod = types.ModuleType("seabreeze")
    seabreeze_mod.__oled_app_simulator__ = True
    seabreeze_mod.use = lambda backend=None: None

    spectrometers_mod = types.ModuleType("seabreeze.spectrometers")
    spectrometers_mod.__oled_app_simulator__ = True
    spectrometers_mod.list_devices = lambda: [_SimulatedSpectrometerDevice()]
    spectrometers_mod.Spectrometer = _SimSpectrometer
    seabreeze_mod.spectrometers = spectrometers_mod

    sys.modules["xtralien"] = xtralien_mod
    sys.modules["seabreeze"] = seabreeze_mod
    sys.modules["seabreeze.spectrometers"] = spectrometers_mod


def uninstall_simulator_modules() -> None:
    for name in ["xtralien", "seabreeze", "seabreeze.spectrometers"]:
        mod = sys.modules.get(name)
        if getattr(mod, "__oled_app_simulator__", False):
            del sys.modules[name]


def prepare_hardware_environment(pixel_id: str, app_settings: Optional[Dict[str, Any]], log: Callable[[str], None]) -> None:
    settings = app_settings or load_app_settings()
    if settings.get("hardware_mode") == HARDWARE_MODE_SIM:
        cfg_path = Path(settings.get("simulator_config_path") or SCRIPT_DIR / SIM_CONFIG_FILE)
        ensure_default_sim_config(cfg_path)
        install_simulator_modules(pixel_id, cfg_path)
        log(f"Режим оборудования: эмулятор. Пиксель {pixel_id}; конфиг {cfg_path}")
    else:
        uninstall_simulator_modules()
        log("Режим оборудования: реальное оборудование. Будут использованы установленные xtralien/seabreeze.")
