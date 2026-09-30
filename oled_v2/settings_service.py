"""Validated application settings exposed by the v2 desktop backend."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from oled_app.constants import (
    HARDWARE_MODE_REAL,
    HARDWARE_MODE_SIM,
    RAW_DATA_FOLDER,
    RAW_DATA_POLICY_DELETE_AFTER_XLSX,
    RAW_DATA_POLICY_KEEP_SEPARATE,
)
from oled_app.settings import (
    DEFAULT_APP_SETTINGS,
    app_settings_path,
    load_app_settings,
    save_app_settings,
)


class SettingsValidationError(ValueError):
    """Raised when an editable settings document is invalid."""


EDITABLE_SECTIONS = (
    "measurement_units",
    "spectral_calibration",
    "camera",
    "ivl_advanced",
    "spectrum_advanced",
    "stability_advanced",
)


class SettingsService:
    def __init__(
        self,
        loader: Callable[[], Dict[str, Any]] = load_app_settings,
        saver: Callable[[Dict[str, Any]], None] = save_app_settings,
        path_provider: Optional[Callable[[], Path]] = app_settings_path,
    ) -> None:
        self._loader = loader
        self._saver = saver
        self._path_provider = path_provider

    def state(self) -> Dict[str, Any]:
        settings = self._loader()
        result = {
            section: {
                key: deepcopy((settings.get(section) or {}).get(key, default))
                for key, default in DEFAULT_APP_SETTINGS[section].items()
            }
            for section in EDITABLE_SECTIONS
        }
        result.update({
            "default_root": str(settings.get("default_root") or ""),
            "hardware_mode": str(settings.get("hardware_mode") or HARDWARE_MODE_SIM),
            "com_port": str(settings.get("com_port") or "COM3"),
            "auto_com_port": bool(settings.get("auto_com_port", False)),
            "simulator_config_path": str(settings.get("simulator_config_path") or ""),
            "raw_data": deepcopy(settings.get("raw_data") or {}),
        })
        return {
            "settings": result,
            "path": str(self._path_provider()) if self._path_provider else "oled_app_settings.json",
        }

    def update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise SettingsValidationError("Ожидается объект настроек.")
        allowed = {
            "default_root", "hardware_mode", "com_port", "auto_com_port",
            "simulator_config_path", "raw_data", *EDITABLE_SECTIONS,
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise SettingsValidationError(f"Неизвестные разделы настроек: {', '.join(unknown)}.")

        current = self._loader()
        updated = deepcopy(current)
        updated["default_root"] = self._text(payload, "default_root", required=True)
        mode = self._text(payload, "hardware_mode", required=True)
        if mode not in {HARDWARE_MODE_SIM, HARDWARE_MODE_REAL}:
            raise SettingsValidationError("Режим оборудования должен быть simulator или real.")
        updated["hardware_mode"] = mode
        updated["com_port"] = self._text(payload, "com_port", required=True)
        updated["auto_com_port"] = self._boolean(payload, "auto_com_port")
        updated["simulator_config_path"] = self._text(
            payload, "simulator_config_path", required=True,
        )

        raw = self._section(payload, "raw_data")
        self._only(raw, {"policy", "folder_name"}, "raw_data")
        policy = self._text(raw, "policy", required=True)
        if policy not in {RAW_DATA_POLICY_KEEP_SEPARATE, RAW_DATA_POLICY_DELETE_AFTER_XLSX}:
            raise SettingsValidationError("Неизвестный режим хранения сырых CSV.")
        updated["raw_data"] = {"policy": policy, "folder_name": RAW_DATA_FOLDER}

        updated["measurement_units"] = self._numeric_section(
            payload, "measurement_units", {
                "pixel_area_mm2": (0.000001, 1_000_000.0, False),
                "luminance_red_cd_m2_per_uA": (0.0, 1_000_000_000.0, False),
                "luminance_green_cd_m2_per_uA": (0.0, 1_000_000_000.0, False),
                "luminance_blue_cd_m2_per_uA": (0.0, 1_000_000_000.0, False),
                "luminance_white_cd_m2_per_uA": (0.0, 1_000_000_000.0, False),
                "geometric_conversion_coefficient": (0.000001, 1_000_000_000.0, False),
                "integral_conversion_coefficient": (0.000001, 1_000_000_000.0, False),
            },
        )
        updated["spectral_calibration"] = self._numeric_section(
            payload, "spectral_calibration", {
                "median_tolerance_percent": (0.0, 100.0, False),
                "linear_model_outlier_percent": (0.0, 1000.0, False),
            },
        )

        camera = self._section(payload, "camera")
        camera_allowed = {
            "host", "port", "request_timeout_s", "stream_timeout_s",
            "auto_connect_wifi", "wifi_profile", "wifi_interface",
            "wifi_connect_timeout_s", "restore_previous_wifi", "download_dir",
            "keep_remote_files_after_download", "combine_stability_telemetry_video",
            "crop_width_percent", "crop_height_percent", "video_camera_settings",
            "photo_quality_settings", "photo_exposure_settings",
        }
        self._only(camera, camera_allowed, "camera")
        previous_camera = deepcopy(current.get("camera") or {})
        previous_camera.update({
            "host": self._text(camera, "host", required=True),
            "port": self._integer(camera, "port", 1, 65535),
            "request_timeout_s": self._number(camera, "request_timeout_s", 0.1, 300.0),
            "stream_timeout_s": self._number(camera, "stream_timeout_s", 0.1, 300.0),
            "auto_connect_wifi": self._boolean(camera, "auto_connect_wifi"),
            "wifi_profile": self._text(camera, "wifi_profile"),
            "wifi_interface": self._text(camera, "wifi_interface"),
            "wifi_connect_timeout_s": self._number(camera, "wifi_connect_timeout_s", 3.0, 120.0),
            "restore_previous_wifi": self._boolean(camera, "restore_previous_wifi"),
            "download_dir": self._text(camera, "download_dir", required=True),
            "keep_remote_files_after_download": self._boolean(camera, "keep_remote_files_after_download"),
            "combine_stability_telemetry_video": self._boolean(camera, "combine_stability_telemetry_video"),
            "crop_width_percent": self._number(camera, "crop_width_percent", 1.0, 100.0),
            "crop_height_percent": self._number(camera, "crop_height_percent", 1.0, 100.0),
        })
        if previous_camera["auto_connect_wifi"] and not previous_camera["wifi_profile"]:
            raise SettingsValidationError("Для автоподключения задайте имя Wi-Fi-профиля.")
        updated["camera"] = previous_camera

        updated["ivl_advanced"] = self._numeric_section(
            payload, "ivl_advanced", {
                "photodiode_bias_V": (-20.0, 20.0, False),
                "photodiode_range": (1, 10, True),
                "photodiode_threshold_uA": (0.0, 1_000_000.0, False),
                "working_confirmation_points": (1, 100, True),
                "opening_photodiode_threshold_uA": (0.0, 1_000_000.0, False),
                "opening_confirmation_points": (1, 100, True),
                "burnout_current_threshold_mA": (0.000001, 10_000.0, False),
                "no_contact_max_led_current_mA": (0.0, 10_000.0, False),
                "burned_confirmation_cycles": (0, 100, True),
            }, bool_fields={"mark_current_limit_as_burnout"},
        )
        spectrum = self._numeric_section(
            payload, "spectrum_advanced", {
                "photodiode_bias_V": (-20.0, 20.0, False),
                "photodiode_range": (1, 10, True),
                "target_intensity": (0.0, 65535.0, False),
                "intensity_min": (0.0, 65535.0, False),
                "intensity_max": (0.0, 65535.0, False),
                "saturation_level": (0.0, 65535.0, False),
                "min_peak_width_nm": (0.0, 1000.0, False),
                "t_int_initial_s": (0.000001, 30.0, False),
                "t_int_min_s": (0.000001, 30.0, False),
                "t_int_max_s": (0.000001, 30.0, False),
                "kp": (0.0, 1000.0, False),
                "ki": (0.0, 1000.0, False),
                "max_iterations": (1, 1000, True),
                "tolerance": (0.0, 1.0, False),
                "settle_time_voltage_s": (0.0, 3600.0, False),
                "settle_time_spectrum_s": (0.0, 3600.0, False),
                "dark_spectrum_scans": (1, 1000, True),
            }, bool_fields={
                "reuse_previous_integration_time", "discard_first_scan_after_tint_change",
                "dark_spectrum_enabled", "baseline_correction_enabled",
                "peak_detection_enabled",
            }, text_fields={"peak_search_mode_for_tint"},
        )
        if not spectrum["t_int_min_s"] <= spectrum["t_int_initial_s"] <= spectrum["t_int_max_s"]:
            raise SettingsValidationError(
                "Времена интегрирования должны удовлетворять: минимум ≤ начальное ≤ максимум."
            )
        if not spectrum["intensity_min"] <= spectrum["target_intensity"] <= spectrum["intensity_max"]:
            raise SettingsValidationError(
                "Целевая интенсивность должна быть между минимумом и максимумом."
            )
        updated["spectrum_advanced"] = spectrum
        updated["stability_advanced"] = self._numeric_section(
            payload, "stability_advanced", {
                "voltage_step_max": (0.000001, 10.0, False),
                "current_control_kp": (0.000001, 1000.0, False),
                "photodiode_bias_V": (-20.0, 20.0, False),
                "photodiode_threshold_uA": (0.0, 1_000_000.0, False),
                "photodiode_range": (1, 10, True),
            },
        )
        self._saver(updated)
        return self.state()

    @staticmethod
    def _section(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise SettingsValidationError(f"Раздел {key} должен быть объектом.")
        return value

    @staticmethod
    def _only(section: Dict[str, Any], allowed: set[str], label: str) -> None:
        unknown = sorted(set(section) - allowed)
        if unknown:
            raise SettingsValidationError(f"Неизвестные поля {label}: {', '.join(unknown)}.")

    @staticmethod
    def _text(section: Dict[str, Any], key: str, required: bool = False) -> str:
        value = section.get(key)
        if not isinstance(value, str):
            raise SettingsValidationError(f"{key}: требуется строка.")
        value = value.strip()
        if required and not value:
            raise SettingsValidationError(f"{key}: значение не может быть пустым.")
        return value

    @staticmethod
    def _boolean(section: Dict[str, Any], key: str) -> bool:
        value = section.get(key)
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{key}: требуется логическое значение.")
        return value

    @staticmethod
    def _number(section: Dict[str, Any], key: str, low: float, high: float) -> float:
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError(f"{key}: требуется число.")
        value = float(value)
        if not math.isfinite(value) or not low <= value <= high:
            raise SettingsValidationError(f"{key}: допустимо от {low:g} до {high:g}.")
        return value

    @classmethod
    def _integer(cls, section: Dict[str, Any], key: str, low: int, high: int) -> int:
        value = cls._number(section, key, low, high)
        if not value.is_integer():
            raise SettingsValidationError(f"{key}: требуется целое число.")
        return int(value)

    @classmethod
    def _numeric_section(
        cls,
        payload: Dict[str, Any],
        key: str,
        fields: Dict[str, tuple[float, float, bool]],
        bool_fields: Optional[set[str]] = None,
        text_fields: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        section = cls._section(payload, key)
        bool_fields = bool_fields or set()
        text_fields = text_fields or set()
        cls._only(section, set(fields) | bool_fields | text_fields, key)
        result: Dict[str, Any] = {}
        for name, (low, high, integer) in fields.items():
            result[name] = (
                cls._integer(section, name, int(low), int(high))
                if integer else cls._number(section, name, low, high)
            )
        for name in bool_fields:
            result[name] = cls._boolean(section, name)
        for name in text_fields:
            result[name] = cls._text(section, name, required=True)
        return result
