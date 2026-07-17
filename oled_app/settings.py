"""Application settings and simulator config defaults."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from .constants import (
    APP_SETTINGS_FILE,
    DEFAULT_ROOT,
    HARDWARE_MODE_SIM,
    RAW_DATA_FOLDER,
    RAW_DATA_POLICY_KEEP_SEPARATE,
    SCRIPT_DIR,
    SIM_CONFIG_FILE,
)

DEFAULT_SIMULATOR_CONFIG: Dict[str, Any] = {
    "active": True,
    "global": {
        "random_seed": 42,
        "current_noise_relative": 0.01,
        "photodiode_noise_uA": 0.01,
        "voltage_noise_V": 0.002,
        "spectrum_noise_relative": 0.008,
    },
    "default_pixel": {
        "mode": "working",
        "turn_on_voltage_V": 2.65,
        "current_at_5V_mA": 6.0,
        "iv_exponent": 2.0,
        "leakage_mA": 0.0005,
        "max_current_mA": 30.0,
        "short_resistance_ohm": 120.0,
        "burnout_voltage_V": None,
        "burnout_after_s": None,
        "photodiode_gain_uA_per_mA": 0.55,
        "degradation_tau_s": None,
        "voltage_drift_V_per_s": 0.0,
        "spectrum": {
            "dark_offset_counts": 120.0,
            "dark_counts_per_s": 250.0,
            "background_counts_per_s": 50.0,
            "counts_per_mA_per_s": 1_200_000.0,
            "saturation_counts": 65535.0,
            "peaks": [
                {"center_nm": 465.0, "fwhm_nm": 42.0, "amplitude": 0.75},
                {"center_nm": 535.0, "fwhm_nm": 70.0, "amplitude": 1.00},
                {"center_nm": 625.0, "fwhm_nm": 85.0, "amplitude": 0.62},
            ],
        },
    },
    "pixels": {
        "Q1_1_1": {"mode": "working", "turn_on_voltage_V": 2.60, "current_at_5V_mA": 6.5, "photodiode_gain_uA_per_mA": 0.60},
        "Q1_1_2": {"mode": "weak", "photodiode_gain_uA_per_mA": 0.05, "spectrum": {"counts_per_mA_per_s": 130_000.0}},
        "Q1_1_3": {"mode": "nonworking", "photodiode_gain_uA_per_mA": 0.0},
        "Q1_1_4": {"mode": "no_contact", "leakage_mA": 0.001},
        "Q1_2_1": {"mode": "working", "burnout_voltage_V": 3.45, "short_resistance_ohm": 80.0},
        "Q1_2_2": {"mode": "working", "degradation_tau_s": 35.0, "voltage_drift_V_per_s": 0.001},
        "Q1_2_3": {
            "mode": "working",
            "turn_on_voltage_V": 2.75,
            "current_at_5V_mA": 5.5,
            "spectrum": {
                "peaks": [
                    {"center_nm": 455.0, "fwhm_nm": 38.0, "amplitude": 0.95},
                    {"center_nm": 540.0, "fwhm_nm": 72.0, "amplitude": 0.90},
                    {"center_nm": 610.0, "fwhm_nm": 92.0, "amplitude": 0.70},
                ]
            },
        },
    },
}

DEFAULT_APP_SETTINGS: Dict[str, Any] = {
    "default_root": str(SCRIPT_DIR / DEFAULT_ROOT),
    "hardware_mode": HARDWARE_MODE_SIM,
    "com_port": "SIM",
    "auto_com_port": False,
    "simulator_config_path": str(SCRIPT_DIR / SIM_CONFIG_FILE),
    "camera": {
        "host": "192.168.4.1",
        "port": 8765,
        "request_timeout_s": 8.0,
        "stream_timeout_s": 12.0,
        "download_dir": str(SCRIPT_DIR / "camera_downloads"),
        "keep_remote_files_after_download": True,
        "crop_width_percent": 100.0,
        "crop_height_percent": 100.0,
        "video_camera_settings": {},
        "photo_quality_settings": {},
    },
    "ivl_advanced": {
        "photodiode_bias_V": -5.0,
        "photodiode_range": 4,
        "photodiode_threshold_uA": 0.5,
        "burnout_current_threshold_mA": 10.0,
        "mark_current_limit_as_burnout": False,
        "no_contact_max_led_current_mA": 0.05,
        "burned_confirmation_cycles": 1,
    },
    "spectrum_advanced": {
        "photodiode_bias_V": -5.0,
        "photodiode_range": 4,
        "target_intensity": 40000.0,
        "intensity_min": 20000.0,
        "intensity_max": 55000.0,
        "saturation_level": 60000.0,
        "min_peak_width_nm": 15.0,
        "t_int_initial_s": 0.01,
        "t_int_min_s": 0.001,
        "t_int_max_s": 10.0,
        "reuse_previous_integration_time": True,
        "discard_first_scan_after_tint_change": True,
        "kp": 0.3,
        "ki": 0.05,
        "max_iterations": 20,
        "tolerance": 0.05,
        "peak_search_mode_for_tint": "auto",
        "settle_time_voltage_s": 0.1,
        "settle_time_spectrum_s": 0.05,
        "dark_spectrum_enabled": False,
        "dark_spectrum_scans": 3,
        "baseline_correction_enabled": True,
        "peak_detection_enabled": False,
    },
    "measurement_units": {
        "pixel_area_mm2": 1.0,
        "luminance_red_cd_m2_per_uA": 1.0,
        "luminance_green_cd_m2_per_uA": 1.0,
        "luminance_blue_cd_m2_per_uA": 1.0,
    },
    "raw_data": {
        "policy": RAW_DATA_POLICY_KEEP_SEPARATE,
        "folder_name": RAW_DATA_FOLDER,
    },
    "stability_advanced": {
        "voltage_step_max": 0.02,
        "current_control_kp": 0.01,
        "photodiode_bias_V": -5.0,
        "photodiode_threshold_uA": 0.1,
        "photodiode_range": 4,
    },
    "ui": {
        "last_ivl_graph_mode": "raw",
    },
    "measurement_defaults": {
        "ivl": {
            "sweep_start_V": "0",
            "sweep_end_V": "5",
            "step_V": "0.02",
            "time_per_point_s": "0.01",
            "cycles": "1",
            "delay_between_cycles_s": "1",
            "current_limit_mA": "10",
        },
        "spectrum": {
            "voltage_end_V": "5",
            "voltage_step_V": "0.1",
            "current_limit_mA": "6",
            "led_type": "auto",
            "use_opening_voltage": True,
        },
        "stability": {
            "control_mode": "current",
            "current_setpoint_mA": "3.5",
            "voltage_setpoint_V": "3.5",
            "voltage_limit_V": "5",
            "current_limit_mA": "10",
            "measurement_time_s": "86400",
            "sample_interval_s": "1",
            "autosave_interval_s": "600",
        },
    },
}


def deep_update(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_update(out[key], value)
        else:
            out[key] = value
    return out


def app_settings_path() -> Path:
    return SCRIPT_DIR / APP_SETTINGS_FILE


def load_app_settings() -> Dict[str, Any]:
    path = app_settings_path()
    settings = deepcopy(DEFAULT_APP_SETTINGS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            settings = deep_update(settings, loaded)
        except Exception:
            pass
    return settings


def save_app_settings(settings: Dict[str, Any]) -> None:
    app_settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_default_sim_config(config_path: Optional[Path] = None) -> Path:
    path = Path(config_path) if config_path else SCRIPT_DIR / SIM_CONFIG_FILE
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_SIMULATOR_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def hardware_mode_label(settings: Dict[str, Any]) -> str:
    return "Эмулятор" if settings.get("hardware_mode") == HARDWARE_MODE_SIM else "Реальное оборудование"
