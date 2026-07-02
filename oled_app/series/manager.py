"""Series creation and opening."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from oled_app.constants import APP_VERSION, CONFIG_FILE
from oled_app.series.journal import SeriesJournal
from oled_app.series.metadata import (
    luminance_coefficient_for_color,
    normalize_quarter_payload,
    quarter_led_color,
)
from oled_app.utils import now_str, safe_filename


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
    ) -> None:
        quarter_payload = normalize_quarter_payload(quarter_bases, quarter_descriptions, quarter_led_colors)
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

    def luminance_coefficient_for_pixel(self, pixel_id: str, app_settings: Dict[str, Any]) -> float:
        row = self.journal.get_pixel(pixel_id) or {}
        quarter_number = int(row.get("Quarter number") or 1)
        color = quarter_led_color(self.config, quarter_number)
        return luminance_coefficient_for_color(app_settings, color)
