# Codex Project Map

This file is written for Codex agents. Read it before editing the project.

## Current Version

- Current app version: `v1.8.0-beta`
- Python source of truth: `APP_VERSION` in `oled_app/constants.py`
- Human changelog: `CHANGELOG.md`
- Version archive: `docs/versions/`
- Structured manifest: `docs/project_manifest.json`

The current prerelease line is `v1.8.0-beta`. It introduces series/pixel-bound camera capture and dynamic current/voltage stability control; publish it as a GitHub prerelease.

## Main Entry Points

- Modular application launcher: `oled_modular_app.py`
- Reference application: `oled_measurement_app_v2_5.py`
- Modular package: `oled_app/`
- Status-only smoke check: `python oled_modular_app.py --status`
- GUI framework: `tkinter` / `ttk`
- Excel output and journals: `openpyxl`
- Real hardware modules are imported lazily: `xtralien`, `seabreeze`
- Built-in simulator is in the same Python file and can run without hardware.
- Shared constants, settings defaults, settings I/O, and common utility helpers now live in `oled_app/constants.py`, `oled_app/settings.py`, and `oled_app/utils.py`.
- Series creation, quarter metadata/naming, journal handling, measurement paths, status colors, and holder layout now live under `oled_app/series/`.
- Hardware probing, Ossila auto-COM, simulator module installation, SMU shutdown helper, and spectrometer discovery now live under `oled_app/hardware/`.
- The desktop camera HTTP client lives in `oled_app/camera/client.py`; free and series-bound beta workflows live in `oled_app/gui/camera_window.py`.
- The Raspberry Pi FastAPI service, dynamic gPhoto2 JPEG-quality discovery, FFmpeg video profiles, verified-download deletion API, example config, systemd unit for Linux user `user`, and setup guide live under `raspberry_camera_service/`.
- The camera-dependent photo/video quality design and implementation notes live in `docs/camera_quality_research.md`.
- Origin report preparation now lives in `oled_app/reports/origin_report.py`; `scripts/build_report_origin_workbook.py` is a CLI wrapper.
- IVL / ВАЯХ measurement workflow now lives in `oled_app/measurements/ivl.py` for the modular application.
- Raw CSV measurement helpers now live in `oled_app/measurements/raw_io.py`; IVL, Spectrum, and Stability post-processing live in `oled_app/processing/ivl_results.py`, `oled_app/processing/spectrum_results.py`, and `oled_app/processing/stability_results.py`.
- Spectrum measurement workflow now lives in `oled_app/measurements/spectrum.py` for the modular application.
- Stability measurement workflow now lives in `oled_app/measurements/stability.py` for the modular application.
- Shared Tk helpers, including Windows DPI awareness, screen-bounded geometry and scrollable containers, live in `oled_app/gui/widgets.py`; measurement progress windows live in `oled_app/gui/progress.py`.
- The modular GUI shell, start screen, series measurement menu, settings window, camera beta window, IVL window, spectrum window, dynamic stability window, and report window now live under `oled_app/gui/`.

During modularization, do not edit `oled_measurement_app_v2_5.py` unless the user explicitly asks to change the reference app. Build and wire new behavior through `oled_modular_app.py` and `oled_app/`.

## Measurement Workflows

- IVL / ВАЯХ: modular workflow is in `oled_app/measurements/ivl.py` and the modular GUI window is in `oled_app/gui/ivl_window.py`; it writes raw CSV during measurement and builds the compatible final XLSX through `oled_app/processing/ivl_results.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Spectrum: modular workflow is in `oled_app/measurements/spectrum.py` and the modular GUI window is in `oled_app/gui/spectrum_window.py`; it writes raw summary/spectra CSV during measurement and builds the compatible final XLSX through `oled_app/processing/spectrum_results.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Stability: modular workflow is in `oled_app/measurements/stability.py` and the modular GUI window is in `oled_app/gui/stability_window.py`; beta supports mutable constant-current and ramped constant-voltage targets and records both target and applied values in raw CSV/XLSX.
- Report: report builder logic is in `oled_app/reports/origin_report.py`, and the modular GUI window is in `oled_app/gui/report_window.py`; it continues to run `scripts/build_report_origin_workbook.py` for Origin `.opju` reports with one selected substrate and spectrum pixel per series plus selectable voltage grids.
- Series journal: `series_journal.xlsx` inside each series folder.

Generated measurement data belongs in `OLED_series/` and must not be committed.

## Version And Release Workflow

For each release:

1. Bump `APP_VERSION`.
2. Update `README.md`, `PROJECT_OVERVIEW.md`, `CODEX_PROJECT.md`, and `docs/project_manifest.json`.
3. Add a top entry to `CHANGELOG.md`.
4. Add `docs/versions/vX.Y.Z.md`.
5. Run focused checks.
6. Commit and push.
7. Create and push tag `vX.Y.Z`.
8. Create GitHub Release:

```powershell
.\env\Scripts\python.exe .\scripts\create_github_release.py vX.Y.Z
```

The release script uses `gh` when available, otherwise GitHub REST API with `GITHUB_TOKEN` or `GH_TOKEN`.

## Local Git Notes

The repository may use `.git-local` instead of a normal `.git` directory. If Git cannot find the repo, set:

```powershell
$env:GIT_DIR = ".git-local"
$env:GIT_WORK_TREE = (Get-Location).Path
```

Use the Visual Studio bundled Git if plain `git` is not on PATH.

## Do Not Commit

- `OLED_series/`
- `oled_app_settings.json`
- `.venv/`, `env/`
- `__pycache__/`
- `Backup/`
- local IDE/cache files
