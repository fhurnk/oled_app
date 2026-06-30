# Codex Project Map

This file is written for Codex agents. Read it before editing the project.

## Current Version

- Current app version: `v1.7.2`
- Python source of truth: `APP_VERSION` in `oled_app/constants.py`
- Human changelog: `CHANGELOG.md`
- Version archive: `docs/versions/`
- Structured manifest: `docs/project_manifest.json`

When changing user-visible behavior, update all version references in the same commit.

## Main Entry Points

- Application: `oled_measurement_app_v2_5.py`
- Modular package scaffold: `oled_app/`
- GUI framework: `tkinter` / `ttk`
- Excel output and journals: `openpyxl`
- Real hardware modules are imported lazily: `xtralien`, `seabreeze`
- Built-in simulator is in the same Python file and can run without hardware.
- Shared constants, settings defaults, settings I/O, and common utility helpers now live in `oled_app/constants.py`, `oled_app/settings.py`, and `oled_app/utils.py`.

## Measurement Workflows

- IVL / ВАЯХ: creates current/light curves and pixel status.
- Spectrum: uses known opening voltage, auto-selects integration time, saves raw and processed spectra.
- Stability: runs constant-current stability measurements after IVL.
- Report: GUI button runs `scripts/build_report_origin_workbook.py` for Origin `.opju` reports with selectable spectrum pixels and voltage grids.
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
