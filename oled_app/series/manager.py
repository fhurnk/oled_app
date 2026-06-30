"""Series creation and opening."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from oled_app.constants import APP_VERSION, CONFIG_FILE
from oled_app.series.journal import SeriesJournal
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

        config = {
            "app_version": APP_VERSION,
            "created_at": now_str(),
            "deposition_date": deposition_date,
            "keyword": keyword,
            "quarter_names": {
                str(q): safe_filename(quarter_names.get(str(q), f"Q{q}"), fallback=f"Q{q}")
                for q in range(1, 5)
            },
        }
        (series_folder / CONFIG_FILE).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(series_folder)
