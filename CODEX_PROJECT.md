# Codex Project Map

This file is written for Codex agents. Read it before editing the project.

## Current Version

- Current app version: `v2.0.0-alpha`
- Python source of truth: `APP_VERSION` in `oled_app/constants.py`
- Human changelog: `CHANGELOG.md`
- Version archive: `docs/versions/`
- Structured manifest: `docs/project_manifest.json`

The active prerelease is the mutable `v2.0.0-alpha`, which begins the v2
interface migration from stable `v1.9.1`. All migration checkpoints update the
same tag, version note and GitHub prerelease; do not create `alpha-2`,
`alpha-3`, or another project version unless the user explicitly asks. Stage 0
is recorded in `docs/v2_functional_parity_checklist.md`; Stage 1's tested
FastAPI/React/WebView2 onedir prototype and Stage 2's simulator-first
SMU/spectrometer WebSocket PoC are in `oled_v2_app.py`, `oled_v2/`,
`v2_frontend/`, and `packaging/`. Stage 3's shared tokens, React components,
reference series screen, table/status/dialog patterns, and chart palette are in
`v2_frontend/src/design-system/`. Stage 4's working series API bridge is in
`oled_v2/series_service.py`, and its create/open/edit/map/table/history/thumbnail/
queue interface is in `v2_frontend/src/SeriesWorkspace.tsx`. Real hardware
validation remains pending but does not block Stage 5 software work.
Current progress is in
`docs/v2_migration_status.md`. The modular Tkinter launcher remains the default
application until v2 passes the full parity and hardware checklist.

## Main Entry Points

- Modular application launcher: `oled_modular_app.py`
- Reference application: `oled_measurement_app_v2_5.py`
- Modular package: `oled_app/`
- Isolated v2 launcher: `oled_v2_app.py`
- v2 backend/desktop package: `oled_v2/`
- v2 React/Vite source: `v2_frontend/`
- v2 onedir build: `scripts/build_v2_alpha.ps1`
- v2 status/smoke: `python oled_v2_app.py --status`,
  `python oled_v2_app.py --backend-smoke`, and
  `python oled_v2_app.py --poc-smoke` / `--series-smoke`
- v2 Stage 2 coordinator: `oled_v2/poc.py`
- v2 Stage 3 design system: `v2_frontend/src/design-system/`
- v2 Stage 4 series bridge: `oled_v2/series_service.py`
- v2 Stage 4 series workspace: `v2_frontend/src/SeriesWorkspace.tsx`
- v2 implementation plan: `docs/v2_interface_plan.md`
- v2 migration status: `docs/v2_migration_status.md`
- v1.9.1 → v2 parity checklist: `docs/v2_functional_parity_checklist.md`
- Status-only smoke check: `python oled_modular_app.py --status`
- GUI framework: `tkinter` / `ttk`
- Excel output and journals: `openpyxl`
- Real hardware modules are imported lazily: `xtralien`, `seabreeze`
- Built-in simulator is in the same Python file and can run without hardware.
- Shared constants, settings defaults, settings I/O, and common utility helpers now live in `oled_app/constants.py`, `oled_app/settings.py`, and `oled_app/utils.py`.
- Series creation, quarter metadata/naming, journal handling, measurement paths, status colors, and holder layout now live under `oled_app/series/`.
- Hardware probing, Ossila auto-COM, simulator module installation, SMU shutdown helper, and spectrometer discovery now live under `oled_app/hardware/`.
- The desktop camera HTTP client lives in `oled_app/camera/client.py`; synchronized stability-video rendering lives in `oled_app/camera/telemetry_video.py`; free and series-bound workflows live in `oled_app/gui/camera_window.py`.
- The Raspberry Pi FastAPI service, dynamic gPhoto2 JPEG-quality and exposure-control discovery, FFmpeg video profiles, verified-download deletion API, example config, systemd unit for Linux user `user`, and setup guide live under `raspberry_camera_service/`.
- The camera-dependent photo/video quality design and implementation notes live in `docs/camera_quality_research.md`.
- Origin report preparation now lives in `oled_app/reports/origin_report.py`; `scripts/build_report_origin_workbook.py` is a CLI wrapper.
- IVL / ВАЯХ measurement workflow now lives in `oled_app/measurements/ivl.py` for the modular application.
- Raw CSV measurement helpers now live in `oled_app/measurements/raw_io.py`; IVL, Spectrum, and Stability post-processing live in `oled_app/processing/ivl_results.py`, `oled_app/processing/spectrum_results.py`, and `oled_app/processing/stability_results.py`.
- On-demand CIE/BPW34 recalculation lives in `oled_app/processing/spectral_calibration.py`; it must write `SPECTRAL_RECALC_*.xlsx` and must not modify the source spectrum workbook.
- Existing-series luminance migration lives in `oled_app/processing/luminance_recalculation.py`; IVL thumbnails live in `oled_app/processing/ivl_preview.py`.
- Spectrum measurement workflow now lives in `oled_app/measurements/spectrum.py` for the modular application.
- Stability measurement workflow now lives in `oled_app/measurements/stability.py` for the modular application.
- Shared Tk helpers, including Windows DPI awareness, screen-bounded geometry and scrollable containers, live in `oled_app/gui/widgets.py`; measurement progress windows live in `oled_app/gui/progress.py`.
- The modular GUI shell, start screen, series measurement menu, settings window, camera window, IVL window, spectrum window, dynamic stability window, and report window now live under `oled_app/gui/`.

During modularization, do not edit `oled_measurement_app_v2_5.py` unless the user explicitly asks to change the reference app. Build and wire new behavior through `oled_modular_app.py` and `oled_app/`.

## Measurement Workflows

- IVL / ВАЯХ: modular workflow is in `oled_app/measurements/ivl.py` and the modular GUI window is in `oled_app/gui/ivl_window.py`; it writes raw CSV during measurement and builds the compatible final XLSX through `oled_app/processing/ivl_results.py`; the reference GUI still uses `oled_measurement_app_v2_5.py`.
- Spectrum: modular workflow is in `oled_app/measurements/spectrum.py` and the modular GUI window is in `oled_app/gui/spectrum_window.py`; it previews integration-time trials, supports safe stopping, writes raw summary/spectra CSV during measurement, builds the compatible final XLSX through `oled_app/processing/spectrum_results.py`, prioritizes pixels marked in the journal, and can capture a substrate from an arbitrary starting pixel or only its marked pixels. Spectral sensitivity correction is never automatic and is launched separately from the series menu.
- Stability: modular workflow is in `oled_app/measurements/stability.py` and the modular GUI window is in `oled_app/gui/stability_window.py`; it supports mutable constant-current feedback and immediate constant-voltage targets and records both target and applied values in raw CSV/XLSX.
- Report: report builder logic is in `oled_app/reports/origin_report.py`, and the modular GUI window is in `oled_app/gui/report_window.py`; it runs `scripts/build_report_origin_workbook.py` for full, IVL-only, or spectra-only Origin `.opju` reports, supports excluding holder quarters 1–4 from every section, and uses one selected substrate and spectrum pixel per included series plus selectable voltage grids when spectra are included.
- Series journal: `series_journal.xlsx` inside each series folder.

Generated measurement data belongs in `OLED_series/` and must not be committed.

## Version And Release Workflow

For a normal release:

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

During the v2 migration, keep `APP_VERSION` at `2.0.0-alpha`, move the existing
`v2.0.0-alpha` tag to each published checkpoint, and update the same prerelease:

```powershell
.\env\Scripts\python.exe .\scripts\create_github_release.py v2.0.0-alpha --prerelease --latest false --update-existing
```

Run every network publication command outside the restricted Codex sandbox: `gh auth status`, branch and tag pushes, and GitHub Release creation or editing. From Codex, request unsandboxed/escalated execution so the process can use the real `%APPDATA%`, Windows Credential Manager, and keyring. A failed authentication check inside the sandbox is not authoritative; repeat it outside the sandbox before asking the user to log in again.

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
