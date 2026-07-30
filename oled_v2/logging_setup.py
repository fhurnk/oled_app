"""Production-style local logging for the v2 launcher."""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_RETENTION_DAYS = 30
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def log_directory() -> Path:
    override = os.environ.get("OLED_V2_LOG_DIR")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "OLED Measurement App" / "logs"


def remove_expired_logs(folder: Path, retention_days: int = LOG_RETENTION_DAYS) -> None:
    cutoff = time.time() - max(1, int(retention_days)) * 24 * 60 * 60
    if not folder.exists():
        return
    for candidate in folder.glob("*.log*"):
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue


def configure_logging() -> logging.Logger:
    folder = log_directory()
    folder.mkdir(parents=True, exist_ok=True)
    remove_expired_logs(folder)

    logger = logging.getLogger("oled_v2")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            folder / "oled-v2.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    return logger
