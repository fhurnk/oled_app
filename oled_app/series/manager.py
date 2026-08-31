"""Series creation and opening."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from oled_app.constants import APP_VERSION, CONFIG_FILE
from oled_app.series.journal import SeriesJournal
from oled_app.series.paths import ensure_quarter_calibration_folder
from oled_app.series.metadata import (
    default_integral_conversion_coefficient,
    geometric_conversion_coefficient,
    luminance_coefficient_for_color,
    normalize_quarter_payload,
    quarter_led_color,
)
from oled_app.utils import (
    SPECTRAL_CALIBRATION_METHODS,
    as_float_or_none,
    luminance_coefficient_at_voltage,
    now_str,
    safe_filename,
)


class SeriesManager:
    def __init__(self, series_folder: Path):
        self.series_folder = Path(series_folder)
        self.config_path = self.series_folder / CONFIG_FILE
        if not self.config_path.exists():
            raise FileNotFoundError(f"В папке нет {CONFIG_FILE}: {self.config_path}")
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.journal = SeriesJournal(self.series_folder, self.config)
        self.journal.initialize_or_update()

    @classmethod
    def create_new(
        cls,
        root_folder: Path,
        deposition_date: str,
        keyword: str,
        quarter_names: Dict[str, str],
        quarter_descriptions: Dict[str, str] | None = None,
        quarter_led_colors: Dict[str, str] | None = None,
        description_scope: str = "quarter",
        half_orientation: str = "top_bottom",
    ) -> "SeriesManager":
        keyword_safe = safe_filename(keyword, fallback="")
        folder_name = f"{deposition_date}"
        if keyword_safe:
            folder_name += f"_{keyword_safe}"
        folder_name = safe_filename(folder_name, fallback="series")

        series_folder = Path(root_folder) / folder_name
        base_folder = series_folder
        suffix = 2
        while series_folder.exists():
            series_folder = Path(f"{base_folder}_{suffix}")
            suffix += 1

        series_folder.mkdir(parents=True, exist_ok=False)
        (series_folder / "measurements").mkdir(exist_ok=True)

        quarter_payload = normalize_quarter_payload(
            quarter_names,
            quarter_descriptions or {},
            quarter_led_colors or {},
            description_scope,
            half_orientation,
        )
        config = {
            "app_version": APP_VERSION,
            "created_at": now_str(),
            "deposition_date": deposition_date,
            "keyword": keyword,
            **quarter_payload,
        }
        (series_folder / CONFIG_FILE).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(series_folder)

    def update_config(
        self,
        deposition_date: str,
        keyword: str,
        quarter_bases: Dict[str, str],
        quarter_descriptions: Dict[str, str],
        quarter_led_colors: Dict[str, str],
        description_scope: str = "quarter",
        half_orientation: str = "top_bottom",
    ) -> None:
        quarter_payload = normalize_quarter_payload(
            quarter_bases,
            quarter_descriptions,
            quarter_led_colors,
            description_scope,
            half_orientation,
        )
        self.config.pop("quarter_layout", None)
        self.config.update(
            {
                "app_version": APP_VERSION,
                "updated_at": now_str(),
                "deposition_date": deposition_date,
                "keyword": keyword,
                **quarter_payload,
            }
        )
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.journal.config = self.config
        self.journal.initialize_or_update()

    def save_config(self) -> None:
        self.config["app_version"] = APP_VERSION
        self.config["updated_at"] = now_str()
        temp_path = self.config_path.with_name(f".{self.config_path.name}.updating")
        temp_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.config_path)
        self.journal.config = self.config

    def luminance_model_for_pixel(
        self,
        pixel_id: str,
        app_settings: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        calibration = self.integral_calibration_for_pixel(pixel_id)
        if (
            calibration is None
            or str(calibration.get("method") or "") not in SPECTRAL_CALIBRATION_METHODS
        ):
            return None
        model = dict(calibration)
        model["integral_coefficient"] = default_integral_conversion_coefficient(
            app_settings
        )
        model["geometric_coefficient"] = geometric_conversion_coefficient(
            app_settings
        )
        row = self.journal.get_pixel(pixel_id) or {}
        opening_voltage = as_float_or_none(row.get("Opening voltage (V)"))
        if opening_voltage is not None:
            model["activation_voltage_V"] = float(opening_voltage)
        return model

    def luminance_coefficient_for_pixel(
        self,
        pixel_id: str,
        app_settings: Dict[str, Any],
        voltage_V: Any = None,
    ) -> float:
        rgb_coefficient = self.rgb_luminance_coefficient_for_pixel(
            pixel_id,
            app_settings,
        )
        coefficient = luminance_coefficient_at_voltage(
            rgb_coefficient,
            voltage_V,
            self.luminance_model_for_pixel(pixel_id, app_settings),
        )
        return float(coefficient if coefficient is not None else rgb_coefficient)

    def rgb_luminance_coefficient_for_pixel(
        self,
        pixel_id: str,
        app_settings: Dict[str, Any],
    ) -> float:
        row = self.journal.get_pixel(pixel_id) or {}
        quarter_number = int(row.get("Quarter number") or 1)
        color = quarter_led_color(self.config, quarter_number)
        return luminance_coefficient_for_color(app_settings, color)

    def integral_coefficient_for_pixel(self, pixel_id: str, app_settings: Dict[str, Any]) -> float:
        configured = default_integral_conversion_coefficient(app_settings)
        calibration = self.integral_calibration_for_pixel(pixel_id)
        if (
            calibration is not None
            and str(calibration.get("method") or "") in SPECTRAL_CALIBRATION_METHODS
        ):
            try:
                return configured * float(calibration.get("coefficient"))
            except (TypeError, ValueError):
                pass
        return configured

    def integral_calibration_for_pixel(self, pixel_id: str) -> Dict[str, Any] | None:
        row = self.journal.get_pixel(pixel_id) or {}
        quarter_number = str(int(row.get("Quarter number") or 1))
        calibrations = self.config.get("quarter_integral_calibrations")
        if isinstance(calibrations, dict):
            calibration = calibrations.get(quarter_number)
            if isinstance(calibration, dict):
                return dict(calibration)
        return None

    def save_quarter_integral_calibration(
        self,
        quarter_number: int,
        calibration: Dict[str, Any],
    ) -> None:
        quarter_number = int(quarter_number)
        calibration_data = dict(calibration)
        calibration_folder = ensure_quarter_calibration_folder(
            self.series_folder,
            self.config,
            quarter_number,
        )
        calibration_path = calibration_folder / "integral_calibration.json"
        calibration_data["calibration_file"] = str(
            calibration_path.relative_to(self.series_folder)
        )
        temp_path = calibration_path.with_name(
            f".{calibration_path.name}.updating"
        )
        temp_path.write_text(
            json.dumps(calibration_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(calibration_path)
        calibrations = self.config.setdefault("quarter_integral_calibrations", {})
        if not isinstance(calibrations, dict):
            calibrations = {}
            self.config["quarter_integral_calibrations"] = calibrations
        calibrations[str(quarter_number)] = calibration_data
        self.save_config()

    @staticmethod
    def geometric_coefficient(app_settings: Dict[str, Any]) -> float:
        return geometric_conversion_coefficient(app_settings)

    @staticmethod
    def configured_integral_coefficient(app_settings: Dict[str, Any]) -> float:
        return default_integral_conversion_coefficient(app_settings)
