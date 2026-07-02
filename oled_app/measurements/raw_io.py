"""Raw CSV helpers used by measurement workflows."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from oled_app.constants import (
    RAW_DATA_FOLDER,
    RAW_DATA_POLICY_DELETE_AFTER_XLSX,
    RAW_DATA_POLICY_KEEP_SEPARATE,
)
from oled_app.utils import safe_filename


def raw_data_settings(app_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    settings = (app_settings or {}).get("raw_data", {})
    if not isinstance(settings, dict):
        settings = {}
    return {
        "policy": settings.get("policy") or RAW_DATA_POLICY_KEEP_SEPARATE,
        "folder_name": settings.get("folder_name") or RAW_DATA_FOLDER,
    }


def should_delete_raw_files(app_settings: Optional[Dict[str, Any]]) -> bool:
    return raw_data_settings(app_settings)["policy"] == RAW_DATA_POLICY_DELETE_AFTER_XLSX


def raw_output_dir(output_dir: Path, app_settings: Optional[Dict[str, Any]]) -> Path:
    settings = raw_data_settings(app_settings)
    folder_name = safe_filename(str(settings.get("folder_name") or RAW_DATA_FOLDER), fallback=RAW_DATA_FOLDER)
    folder = Path(output_dir) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def raw_csv_path(output_dir: Path, filename: str, app_settings: Optional[Dict[str, Any]]) -> Path:
    return raw_output_dir(output_dir, app_settings) / safe_filename(filename, fallback="measurement_raw.csv")


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "YES" if value else "NO"
    return value


class RawCsvWriter:
    """Small DictWriter wrapper that flushes after every measurement point."""

    def __init__(self, path: Path, fieldnames: Sequence[str], fsync: bool = False):
        self.path = Path(path)
        self.fieldnames = list(fieldnames)
        self.fsync = bool(fsync)
        self._file = None
        self._writer: Optional[csv.DictWriter] = None

    def __enter__(self) -> "RawCsvWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=self.fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        self._writer.writeheader()
        self.flush()
        return self

    def writerow(self, row: Mapping[str, Any]) -> None:
        if self._writer is None:
            raise RuntimeError("RawCsvWriter is not open")
        self._writer.writerow({key: csv_value(row.get(key)) for key in self.fieldnames})
        self.flush()

    def writerows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            self.writerow(row)

    def flush(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        if self.fsync:
            os.fsync(self._file.fileno())

    def close(self) -> None:
        if self._file is None:
            return
        self.flush()
        self._file.close()
        self._file = None
        self._writer = None

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def cleanup_raw_files(
    paths: Iterable[Path],
    app_settings: Optional[Dict[str, Any]],
    log: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    raw_paths = [Path(path) for path in paths if path]
    if not should_delete_raw_files(app_settings):
        return raw_paths

    for path in raw_paths:
        try:
            if path.exists():
                path.unlink()
                if log is not None:
                    log(f"Raw CSV удален после сборки XLSX: {path.name}")
        except Exception as exc:
            if log is not None:
                log(f"Не удалось удалить raw CSV {path}: {exc}")

    for folder in sorted({path.parent for path in raw_paths}, key=lambda item: len(item.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return []
