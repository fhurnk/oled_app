"""Shared constants for the OLED measurement application."""

from __future__ import annotations

from pathlib import Path

APP_VERSION = "1.8.0-alpha.1"
SCRIPT_DIR = Path(__file__).resolve().parent.parent

CONFIG_FILE = "series_config.json"
JOURNAL_FILE = "series_journal.xlsx"
DEFAULT_ROOT = "OLED_series"
APP_SETTINGS_FILE = "oled_app_settings.json"
SIM_CONFIG_FILE = "oled_simulator_config.json"
RAW_DATA_FOLDER = "raw_data"

HARDWARE_MODE_REAL = "real"
HARDWARE_MODE_SIM = "simulator"
RAW_DATA_POLICY_KEEP_SEPARATE = "keep_separate"
RAW_DATA_POLICY_DELETE_AFTER_XLSX = "delete_after_xlsx"

MEASUREMENT_FOLDER_NAMES = {
    "IVL": "01_IVL_VAH",
    "SPECTRUM": "02_SPECTRA",
    "STABILITY": "03_STABILITY",
}

PIXELS_SHEET = "Pixels"
MEASUREMENTS_SHEET = "Measurements"
SERIES_SHEET = "Series"
QUARTERS_SHEET = "Quarters"

PIXEL_HEADERS = [
    "Pixel ID",
    "Quarter code",
    "Quarter number",
    "Quarter description",
    "LED color",
    "Substrate number",
    "Pixel number",
    "Last status",
    "Opening voltage (V)",
    "Last IVL date",
    "Last IVL file",
    "Last IVL max current (mA)",
    "Last IVL max photodiode (uA)",
    "Last spectrum date",
    "Last spectrum file",
    "Last spectrum peak count",
    "Last spectrum peaks nm",
    "Last spectrum max intensity (counts/s)",
    "Last stability date",
    "Last stability file",
    "Last updated",
]

MEASUREMENT_HEADERS = [
    "Date time",
    "Measurement day",
    "Type",
    "Pixel ID",
    "Status",
    "File",
    "Params JSON",
    "Notes",
]
