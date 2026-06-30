# Codex Project Map

This file is written for Codex agents. Read it before editing the project.

## Current Version

- Current app version: `v1.7.2`
- Python source of truth: `APP_VERSION` in `oled_app/constants.py`
- Human changelog: `CHANGELOG.md`
- Version archive: `docs/versions/`
- Structured manifest: `docs/project_manifest.json`

When changing user-visible behavior, update all version references in the same commit. During the current prerelease modularization, keep migration progress on the existing `v1.7.2` release/tag and do not create later modularization releases unless the user explicitly asks.

## Main Entry Points

- Modular application scaffold: `oled_modular_app.py`
- Reference application: `oled_measurement_app_v2_5.py`
- Modular package: `oled_app/`
- GUI framework: `tkinter` / `ttk`
- Excel output and journals: `openpyxl`
- Real hardware modules are imported lazily: `xtralien`, `seabreeze`
- Built-in simulator is in the same Python file and can run without hardware.
- Shared constants, settings defaults, settings I/O, and common utility helpers now live in `oled_app/constants.py`, `oled_app/settings.py`, and `oled_app/utils.py`.
- Series creation, journal handling, measurement paths, status colors, and holder layout now live under `oled_app/series/`.
- Hardware probing, Ossila auto-COM, simulator module installation, SMU shutdown helper, and spectrometer discovery now live under `oled_app/hardware/`.
- Origin report preparation now lives in `oled_app/reports/origin_report.py`; `scripts/build_report_origin_workbook.py` is a CLI wrapper.
- IVL / ВАЯХ measurement workflow now lives in `oled_app/measurements/ivl.py` for the modular scaffold.
- Spectrum measurement workflow now lives in `oled_app/measurements/spectrum.py` for the modular scaffold.
- Stability measurement workflow now lives in `oled_app/measurements/stability.py` for the modular scaffold.
- Shared Tk helpers and measurement progress windows now live in `oled_app/gui/widgets.py` and `oled_app/gui/progress.py`.
- The modular GUI shell, start screen, and series measurement menu now live in `oled_app/gui/app.py`, `oled_app/gui/start_screen.py`, and `oled_app/gui/measurement_menu.py`.

During modularization, do not edit `oled_measurement_app_v2_5.py` unless the user explicitly asks to change the reference app. Build and wire new behavior through `oled_modular_app.py` and `oled_app/`. Keep this modularization line on `v1.7.2`.

## Measurement Workflows

- IVL / ВАЯХ: modular workflow is prepared in `oled_app/measurements/ivl.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Spectrum: modular workflow is prepared in `oled_app/measurements/spectrum.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Stability: modular workflow is prepared in `oled_app/measurements/stability.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Report: report builder logic is in `oled_app/reports/origin_report.py`; the GUI can continue to run `scripts/build_report_origin_workbook.py` for Origin `.opju` reports with selectable spectrum pixels and voltage grids.
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
