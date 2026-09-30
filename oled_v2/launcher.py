"""Desktop launcher for the v2 technical prototype."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from oled_app.constants import APP_VERSION

from .config import CLIENT_HEADER, SESSION_HEADER, default_static_root
from .logging_setup import configure_logging, log_directory
from .server import LocalBackend


def console_write(value: str, error: bool = False) -> None:
    """Write CLI diagnostics only when the process has an attached console."""
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        stream.write(str(value) + "\n")
        stream.flush()
    except (AttributeError, OSError, RuntimeError):
        # PyInstaller windowed builds can expose a detached placeholder stream.
        return


def webview2_runtime_status(
    candidate_roots: Optional[Iterable[Path]] = None,
) -> dict:
    """Locate an Evergreen or fixed WebView2 Runtime without starting a window."""

    roots = list(candidate_roots) if candidate_roots is not None else []
    if candidate_roots is None:
        fixed_runtime = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER")
        if fixed_runtime:
            roots.append(Path(fixed_runtime))
        for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            value = os.environ.get(variable)
            if value:
                roots.append(Path(value) / "Microsoft" / "EdgeWebView" / "Application")
    for root in roots:
        folder = Path(root)
        direct = folder / "msedgewebview2.exe"
        if direct.is_file():
            return {"available": True, "version": folder.name, "path": str(folder)}
        try:
            versions = sorted(
                (item for item in folder.iterdir() if item.is_dir()),
                key=lambda item: item.name,
                reverse=True,
            )
        except OSError:
            continue
        for version in versions:
            if (version / "msedgewebview2.exe").is_file():
                return {"available": True, "version": version.name, "path": str(version)}
    return {"available": False, "version": None, "path": None}


def show_windows_error(title: str, message: str) -> None:
    """Display a short native startup error for a console-less Windows build."""

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
    except (AttributeError, OSError):
        return


def dependency_status() -> dict:
    runtime = webview2_runtime_status()
    return {
        "fastapi": importlib.util.find_spec("fastapi") is not None,
        "uvicorn": importlib.util.find_spec("uvicorn") is not None,
        "websockets": importlib.util.find_spec("websockets") is not None,
        "webview": importlib.util.find_spec("webview") is not None,
        "webview2_runtime": runtime["available"],
        "webview2_version": runtime["version"],
        "static_index": (default_static_root() / "index.html").is_file(),
    }


def status_lines() -> list[str]:
    dependencies = dependency_status()
    return [
        f"OLED Measurement App v{APP_VERSION} — v2 technical prototype",
        "Stable default launcher: oled_modular_app.py (Tkinter)",
        f"Frontend build: {'ready' if dependencies['static_index'] else 'missing'}",
        "FastAPI/Uvicorn/WebSocket: "
        + (
            "ready"
            if dependencies["fastapi"]
            and dependencies["uvicorn"]
            and dependencies["websockets"]
            else "missing"
        ),
        f"pywebview/WebView2 bridge: {'ready' if dependencies['webview'] else 'missing'}",
        "WebView2 Runtime: "
        + (
            f"ready ({dependencies['webview2_version']})"
            if dependencies["webview2_runtime"]
            else "missing"
        ),
        f"Logs: {log_directory()}",
    ]


def packaging_smoke() -> int:
    """Verify packaged user-data isolation, write access and WebView2 presence."""

    from oled_app.settings import DEFAULT_APP_SETTINGS, application_data_root, app_settings_path

    root = application_data_root()
    settings = app_settings_path()
    runtime = webview2_runtime_status()
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".oled-v2-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            raise RuntimeError("User data write check returned unexpected content.")
    finally:
        probe.unlink(missing_ok=True)
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        try:
            root.resolve().relative_to(bundled)
        except ValueError:
            pass
        else:
            raise RuntimeError("Packaged user data still points inside the application bundle.")
        managed_paths = (
            Path(DEFAULT_APP_SETTINGS["default_root"]),
            Path(DEFAULT_APP_SETTINGS["simulator_config_path"]),
            Path(DEFAULT_APP_SETTINGS["camera"]["download_dir"]),
        )
        for managed in managed_paths:
            try:
                managed.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    f"Packaged default path is outside the user data root: {managed}"
                ) from exc
    if not runtime["available"]:
        raise RuntimeError("Microsoft Edge WebView2 Runtime is not installed.")
    payload = {
        "packaged": bool(getattr(sys, "frozen", False)),
        "user_data_root": str(root),
        "settings_path": str(settings),
        "default_series_root": str(DEFAULT_APP_SETTINGS["default_root"]),
        "simulator_config_path": str(DEFAULT_APP_SETTINGS["simulator_config_path"]),
        "camera_download_dir": str(DEFAULT_APP_SETTINGS["camera"]["download_dir"]),
        "write_access": True,
        "webview2": runtime["version"],
    }
    report_path = os.environ.get("OLED_V2_PACKAGING_SMOKE_REPORT")
    if report_path:
        report = Path(report_path).expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console_write(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def backend_smoke() -> int:
    logger = configure_logging()
    with LocalBackend(logger=logger) as backend:
        assert backend.session is not None
        headers = {
            SESSION_HEADER: backend.session.token,
            CLIENT_HEADER: "backend-smoke-client-0001",
        }
        request = urllib.request.Request(
            f"{backend.session.origin}/api/app/state",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        settings_request = urllib.request.Request(
            f"{backend.session.origin}/api/settings",
            headers=headers,
        )
        with urllib.request.urlopen(settings_request, timeout=3.0) as response:
            settings_payload = json.loads(response.read().decode("utf-8"))
        if payload.get("application", {}).get("version") != APP_VERSION:
            raise RuntimeError("Backend version does not match APP_VERSION.")
        if "measurement_units" not in settings_payload.get("settings", {}):
            raise RuntimeError("Settings API did not return measurement units.")
        console_write(
            json.dumps(
                {
                    "ready": payload["backend"]["ready"],
                    "version": payload["application"]["version"],
                    "origin": backend.session.origin,
                    "session_id": backend.session.session_id,
                    "settings": "ready",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def poc_smoke() -> int:
    from websockets.sync.client import connect

    logger = configure_logging()
    client_id = "poc-smoke-client-0001"
    with LocalBackend(logger=logger) as backend:
        assert backend.session is not None
        session = backend.session
        ws_url = session.origin.replace("http://", "ws://") + "/api/poc/stream"
        protocols = [
            "oled-v2",
            f"oled-session.{session.token}",
            f"oled-client.{client_id}",
        ]
        headers = {
            SESSION_HEADER: session.token,
            CLIENT_HEADER: client_id,
            "Content-Type": "application/json",
        }

        with connect(
            ws_url,
            origin=session.origin,
            subprotocols=protocols,
            proxy=None,
            open_timeout=3.0,
            close_timeout=1.0,
        ) as websocket:
            snapshot = json.loads(websocket.recv(timeout=3.0))
            if snapshot.get("type") != "poc_snapshot":
                raise RuntimeError("WebSocket did not return the initial PoC snapshot.")

            request = urllib.request.Request(
                f"{session.origin}/api/poc/start",
                data=json.dumps({"point_count": 8, "interval_ms": 5}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=4.0) as response:
                if response.status != 202:
                    raise RuntimeError(f"PoC start returned HTTP {response.status}.")

            terminal_state = None
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                event = json.loads(websocket.recv(timeout=2.0))
                if event.get("type") != "poc_state":
                    continue
                state = event.get("state", {})
                if state.get("status") in {"completed", "stopped", "safety_limit", "failed"}:
                    terminal_state = state
                    break

        if terminal_state is None:
            raise RuntimeError("PoC did not reach a terminal state.")
        if terminal_state.get("status") != "completed":
            raise RuntimeError(f"PoC ended with status {terminal_state.get('status')!r}.")
        if terminal_state.get("point_count") != 8:
            raise RuntimeError("PoC did not stream all eight expected points.")
        if terminal_state.get("safe_shutdown_confirmed") is not True:
            raise RuntimeError("PoC did not confirm safe SMU shutdown.")

        console_write(
            json.dumps(
                {
                    "status": terminal_state["status"],
                    "points": terminal_state["point_count"],
                    "safe_shutdown_confirmed": terminal_state["safe_shutdown_confirmed"],
                    "spectrometer": terminal_state["spectrometer_model"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def ivl_smoke() -> int:
    """Exercise simulator IVL, compatible workbook and shutdown in source or exe."""
    from .ivl import IvlController
    from .series_service import SeriesService
    from openpyxl import load_workbook

    with tempfile.TemporaryDirectory(prefix="oled-v2-ivl-smoke-") as folder:
        controller = IvlController(Path(folder))
        try:
            controller.start({"sweep_end": 0.2, "sweep_increment": 0.1})
            deadline = time.monotonic() + 20
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.05)
            state = controller.snapshot()
            if state["status"] != "completed" or not state["safe_shutdown_confirmed"]:
                raise RuntimeError(f"IVL smoke failed: {state}")
            wb = load_workbook(state["result"]["file"], read_only=True)
            try:
                if "Summary" not in wb.sheetnames or "Cycle_1" not in wb.sheetnames:
                    raise RuntimeError("IVL workbook contract mismatch")
            finally:
                wb.close()
            service = SeriesService(Path(folder) / "series")
            active = service.create_series({"root": str(Path(folder) / "series"),
                "deposition_date": "2026-09-06", "keyword": "ivl-smoke",
                "series_led_color": "green", "quarter_bases": {str(n): "Q" for n in range(1, 5)},
                "quarter_descriptions": {str(n): "Simulator" for n in range(1, 5)}})["active"]
            controller.series_service = service
            controller.start({"sweep_end": 0.2, "sweep_increment": 0.1,
                "target": {"series_path": active["path"], "pixel_id": active["pixels"][0]["pixel_id"]}})
            deadline = time.monotonic() + 20
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.05)
            state = controller.snapshot()
            if state["status"] != "completed" or not state["result"]["journaled"]:
                raise RuntimeError(f"Series IVL smoke failed: {state}")
            queue_start = active["pixels"][-2]["pixel_id"]
            controller.start({
                "sweep_end": 0.2,
                "sweep_increment": 0.1,
                "queue": {
                    "series_path": active["path"],
                    "start_pixel": queue_start,
                    "skip_nonworking": True,
                },
            })
            deadline = time.monotonic() + 30
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                queue_state = controller.snapshot()
                decision = queue_state.get("decision")
                if decision and decision.get("kind") == "queue_no_contact":
                    controller.decide_queue({
                        "run_id": queue_state["run_id"],
                        "decision_id": decision["id"],
                        "action": "continue",
                    })
                time.sleep(0.05)
            state = controller.snapshot()
            if (state["status"] != "completed" or not state.get("queue")
                    or state["queue"]["completed"] != 2 or state["queue"]["remaining"] != 0):
                raise RuntimeError(f"Series IVL queue smoke failed: {state}")
            reopened = service.open_series(active["path"])["active"]
            if reopened["metrics"]["ivl"] != 3 or not reopened["pixels"][0]["thumbnail_available"]:
                raise RuntimeError("Series IVL result/thumbnail was not persisted")
            console_write(json.dumps({"ivl_status": state["status"], "points": len(state["points"]),
                "safe_shutdown_confirmed": state["safe_shutdown_confirmed"],
                "workbook_verified": True, "series_journal_verified": True, "thumbnail_verified": True,
                "queue_completed": state["queue"]["completed"]}))
        finally:
            controller.shutdown()
    return 0


def diagnostics_smoke() -> int:
    """Verify the authenticated diagnostics endpoint and secret filtering."""

    logger = configure_logging()
    with LocalBackend(logger=logger) as backend:
        assert backend.session is not None
        headers = {
            SESSION_HEADER: backend.session.token,
            CLIENT_HEADER: "diagnostics-smoke-client-0001",
        }
        request = urllib.request.Request(
            f"{backend.session.origin}/api/diagnostics",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False)
        if payload.get("application", {}).get("version") != APP_VERSION:
            raise RuntimeError("Diagnostics version does not match APP_VERSION.")
        if backend.session.token in serialized or backend.session.session_id in serialized:
            raise RuntimeError("Diagnostics payload exposed a desktop-session secret.")
        if not payload.get("copy_text") or len(payload.get("operations", [])) < 7:
            raise RuntimeError("Diagnostics payload is incomplete.")
        console_write(
            json.dumps(
                {
                    "version": payload["application"]["version"],
                    "operations": len(payload["operations"]),
                    "recent_errors": len(payload["recent_errors"]),
                    "secrets": "filtered",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def spectrum_smoke() -> int:
    """Exercise simulator T_int optimization, compatible workbook and journal."""
    from openpyxl import load_workbook

    from oled_app.measurements.ivl import IVLParams
    from oled_app.settings import load_app_settings
    from .series_service import SeriesService
    from .spectrum import SpectrumController

    with tempfile.TemporaryDirectory(prefix="oled-v2-spectrum-smoke-") as folder:
        root = Path(folder)
        service = SeriesService(root / "series")
        active = service.create_series({
            "root": str(root / "series"), "deposition_date": "2026-09-27",
            "keyword": "spectrum-smoke", "series_led_color": "green",
            "quarter_bases": {str(number): "Q" for number in range(1, 5)},
            "quarter_descriptions": {str(number): "Simulator" for number in range(1, 5)},
        })["active"]
        controller = SpectrumController(root / "standalone", service)
        common = {
            "voltage_start": 3.0, "voltage_end": 3.1, "voltage_step": 0.1,
            "settle_time_voltage_s": 0.0, "settle_time_spectrum_s": 0.0,
            "discard_first_scan_after_tint_change": False,
        }
        try:
            controller.start(common)
            deadline = time.monotonic() + 25
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.05)
            standalone = controller.snapshot()
            if (standalone["status"] != "completed"
                    or standalone["safe_shutdown_confirmed"] is not True
                    or standalone["point_count"] != 2):
                raise RuntimeError(f"Standalone spectrum smoke failed: {standalone}")
            workbook_path = Path(standalone["result"]["file"])
            workbook = load_workbook(workbook_path, read_only=True)
            try:
                if "Сводка" not in workbook.sheetnames:
                    raise RuntimeError("Spectrum workbook contract mismatch")
            finally:
                workbook.close()

            pixel_id = active["pixels"][0]["pixel_id"]
            controller.start({
                **common,
                "target": {"series_path": active["path"], "pixel_id": pixel_id},
                "use_opening_voltage": False,
            })
            deadline = time.monotonic() + 25
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.05)
            state = controller.snapshot()
            reopened = service.open_series(active["path"])["active"]
            pixel = next(item for item in reopened["pixels"] if item["pixel_id"] == pixel_id)
            if (state["status"] != "completed" or not state["result"]["journaled"]
                    or not pixel["last_spectrum_file"]):
                raise RuntimeError(f"Series spectrum smoke failed: {state}")
            queue_pixels = active["pixels"][1:3]
            for item in queue_pixels:
                target = service.ivl_target(
                    {"series_path": active["path"], "pixel_id": item["pixel_id"]},
                    IVLParams(), load_app_settings(),
                )
                service.record_ivl(target, IVLParams(), {
                    "status": "WORKING",
                    "file": str(Path(active["path"]) / f"{item['pixel_id']}.xlsx"),
                    "run_id": "spectrum-smoke-seed", "ivl_diagnosis": "seed",
                    "opening_voltage": 3.0, "max_current_mA": 1.0, "max_photo_uA": 1.0,
                })
                service.set_spectrum_priority(item["pixel_id"], True)
            controller.start({
                **common,
                "queue": {
                    "series_path": active["path"],
                    "start_pixel": queue_pixels[0]["pixel_id"],
                    "scope": "substrate",
                    "queued_only": True,
                },
                "use_opening_voltage": False,
            })
            deadline = time.monotonic() + 35
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                queue_state = controller.snapshot()
                decision = queue_state.get("decision")
                if decision:
                    action = {
                        "next_pixel": "measure",
                        "no_contact": "continue",
                        "rejected_data": "keep",
                        "replacement": "continue",
                    }.get(decision.get("kind"))
                    if action:
                        controller.decide({
                            "run_id": queue_state["run_id"],
                            "decision_id": decision["id"],
                            "action": action,
                        })
                time.sleep(0.05)
            queue_state = controller.snapshot()
            if (queue_state["status"] != "completed" or not queue_state.get("queue")
                    or queue_state["queue"]["completed"] != 2):
                raise RuntimeError(f"Series spectrum queue smoke failed: {queue_state}")
            console_write(json.dumps({
                "spectrum_status": queue_state["status"], "points": state["point_count"],
                "safe_shutdown_confirmed": queue_state["safe_shutdown_confirmed"],
                "workbook_verified": True, "series_journal_verified": True,
                "t_int_trials_visible": state["latest_spectrum"] is not None,
                "queue_completed": queue_state["queue"]["completed"],
            }, ensure_ascii=False))
        finally:
            controller.shutdown()
    return 0


def stability_smoke() -> int:
    """Exercise simulator stability, live setpoint, workbook and series journal."""
    from openpyxl import Workbook, load_workbook

    from oled_app.measurements.ivl import IVLParams
    from oled_app.settings import load_app_settings
    from .series_service import SeriesService
    from .stability import StabilityController

    with tempfile.TemporaryDirectory(prefix="oled-v2-stability-smoke-") as folder:
        root = Path(folder)
        service = SeriesService(root / "series")
        active = service.create_series({
            "root": str(root / "series"), "deposition_date": "2026-09-28",
            "keyword": "stability-smoke", "series_led_color": "green",
            "quarter_bases": {str(number): "Q" for number in range(1, 5)},
            "quarter_descriptions": {str(number): "Simulator" for number in range(1, 5)},
        })["active"]
        controller = StabilityController(root / "standalone", service)
        common = {
            "control_mode": "voltage", "voltage_setpoint_V": 1.0,
            "voltage_start": 1.0, "voltage_limit": 5.0, "current_limit_mA": 10.0,
            "measurement_time_s": 0.12, "sample_interval_s": 0.01,
            "autosave_interval_s": 60.0,
        }
        try:
            started = controller.start(common)
            deadline = time.monotonic() + 10
            changed = False
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                state = controller.snapshot()
                if state["point_count"] and not changed:
                    controller.setpoint({"run_id": started["run_id"], "value": 1.5})
                    changed = True
                time.sleep(0.02)
            standalone = controller.snapshot()
            if (standalone["status"] != "completed" or not changed
                    or standalone["safe_shutdown_confirmed"] is not True
                    or standalone["result"]["final_setpoint"] != 1.5):
                raise RuntimeError(f"Standalone stability smoke failed: {standalone}")
            workbook = load_workbook(standalone["result"]["file"], read_only=True)
            try:
                if "Data" not in workbook.sheetnames:
                    raise RuntimeError("Stability workbook contract mismatch")
            finally:
                workbook.close()

            pixel_id = active["pixels"][0]["pixel_id"]
            ivl_file = Path(active["path"]) / "stability_smoke_ivl.xlsx"
            seed = Workbook()
            sheet = seed.active
            sheet.title = "Cycle_1"
            sheet.append(["Voltage OLED / LED measured (V)", "Current OLED / LED (mA)"])
            sheet.append([2.0, 0.0])
            sheet.append([4.0, 1.0])
            seed.save(ivl_file)
            ivl_target = service.ivl_target(
                {"series_path": active["path"], "pixel_id": pixel_id},
                IVLParams(), load_app_settings(),
            )
            service.record_ivl(ivl_target, IVLParams(), {
                "status": "WORKING", "file": str(ivl_file), "run_id": "stability-smoke-seed",
                "ivl_diagnosis": "seed", "opening_voltage": 2.0,
                "max_current_mA": 1.0, "max_photo_uA": 1.0,
            })
            controller.start({
                **common,
                "measurement_time_s": 0.08,
                "target": {"series_path": active["path"], "pixel_id": pixel_id},
                "use_ivl_start_voltage": False,
            })
            deadline = time.monotonic() + 10
            while controller.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.02)
            state = controller.snapshot()
            reopened = service.open_series(active["path"])["active"]
            pixel = next(item for item in reopened["pixels"] if item["pixel_id"] == pixel_id)
            if (state["status"] != "completed" or not state["result"]["journaled"]
                    or not pixel["last_stability_file"]):
                raise RuntimeError(f"Series stability smoke failed: {state}")
            console_write(json.dumps({
                "stability_status": state["status"],
                "points": state["point_count"],
                "safe_shutdown_confirmed": state["safe_shutdown_confirmed"],
                "dynamic_setpoint_verified": changed,
                "workbook_verified": True,
                "series_journal_verified": True,
            }, ensure_ascii=False))
        finally:
            controller.shutdown()
    return 0


def camera_smoke() -> int:
    """Exercise camera connection, controls, capture, verified download and stream."""
    import threading

    from oled_app.camera.client import RemoteFile
    from .camera import CameraController
    from .camera_workflow import GuidedCameraWorkflow

    class SmokeCameraClient:
        def __init__(self, base_url: str, timeout_s: float, stream_timeout_s: float):
            self.base_url = base_url
            self.stop_calls = 0
            self.recording_active = False
            self.files = [RemoteFile("smoke-1", "smoke.jpg", "photo", 16)]

        def health(self):
            return {"status": "ok", "service": "smoke-camera"}

        def initialize(self):
            return {"success": True}

        def status(self):
            return {"model": "Smoke Canon", "recording_active": self.recording_active}

        def capabilities(self):
            return {
                "photo_controls": [{
                    "path": "/imageformat", "label": "JPEG", "current": "Fine JPEG",
                    "choices": ["Fine JPEG", "Normal JPEG"],
                }],
                "exposure_controls": [],
                "video_quality_controls": [{
                    "path": "/moviesize", "label": "Видео", "current": "1080p",
                    "choices": ["1080p", "720p"],
                }],
                "video_fps_controls": [],
            }

        def list_files(self):
            return list(self.files)

        def start_liveview(self, _settings):
            return {"success": True}

        def iter_liveview_frames(self, stop_event: threading.Event, on_frame):
            on_frame(b"\xff\xd8camera-smoke\xff\xd9")
            stop_event.wait(3.0)

        def close_liveview_stream(self):
            return None

        def stop_liveview(self):
            self.stop_calls += 1
            return {"success": True}

        def start_recording(self, _settings, _crop):
            self.recording_active = True
            return self.status()

        def stop_recording(self):
            self.recording_active = False
            remote = RemoteFile("video-1", "camera-smoke.mp4", "video", 16)
            self.files.append(remote)
            return remote

        def save_liveview_snapshot(self, file_name, crop):
            remote = RemoteFile("captured-1", f"{file_name or 'capture'}.jpg", "snapshot", 16)
            self.files.append(remote)
            return remote

        def capture_photo(self, _settings, file_name, _crop):
            remote = RemoteFile(
                f"photo-{len(self.files)}", f"{file_name or 'photo'}.jpg", "photo", 16
            )
            self.files.append(remote)
            return remote

        def download_file(self, remote, output_dir, preferred_name=""):
            target = Path(output_dir) / (preferred_name or remote.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"verified-content")
            return target

        def delete_file(self, remote):
            file_id = remote.file_id if isinstance(remote, RemoteFile) else str(remote)
            self.files = [item for item in self.files if item.file_id != file_id]
            return {"success": True}

    class SmokeSeriesService:
        def __init__(self, root: Path):
            self.root = root
            self.records = []

        def camera_target(self, target, station):
            return {
                **target, "series_name": "camera-smoke-series",
                "pixel_row": {"Pixel ID": target["pixel_id"]},
                "station": station, "station_label": "ВАЯХ",
                "journal_type": "CAMERA_IVL",
            }

        def create_camera_session(self, target, station):
            context = self.camera_target(target, station)
            session = self.root / "04_CAMERA" / target["pixel_id"] / "1"
            session.mkdir(parents=True, exist_ok=False)
            return {**context, "session_dir": str(session)}

        def record_camera_file(self, context, media_kind, file_path, remote_name, extra_params=None):
            self.records.append((context, media_kind, Path(file_path), remote_name, extra_params))

    class SmokeMeasurement:
        def __init__(self):
            self.state = {"active": False, "status": "idle", "run_id": None, "result": None}

        def preflight(self, payload):
            return {"target": payload.get("target")}

        def start(self, _payload):
            self.state = {
                "active": True, "status": "running", "run_id": "camera-smoke-guided",
                "result": None,
            }
            threading.Thread(target=self._complete, daemon=True).start()
            return dict(self.state)

        def _complete(self):
            time.sleep(0.03)
            self.state = {
                "active": False, "status": "completed", "run_id": "camera-smoke-guided",
                "result": {"status": "WORKING", "file": "smoke.xlsx", "events": []},
            }

        def snapshot(self):
            return dict(self.state)

        def stop(self):
            self.state["active"] = False
            self.state["status"] = "stopped"
            return dict(self.state)

    download_root = tempfile.TemporaryDirectory(prefix="oled-v2-camera-smoke-")
    series_service = SmokeSeriesService(Path(download_root.name) / "series")
    controller = CameraController(
        client_factory=SmokeCameraClient,
        default_download_dir=download_root.name,
        series_service=series_service,
    )
    try:
        connected = controller.connect("smoke-camera.local", 8765)
        controller.start_liveview()
        deadline = time.monotonic() + 3.0
        while controller.snapshot()["frame_sequence"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        streamed = controller.snapshot()
        if (not connected["connected"] or not connected["initialized"]
                or len(connected["files"]) != 1 or streamed["frame_sequence"] != 1
                or not controller.frame().startswith(b"\xff\xd8")):
            raise RuntimeError(f"Camera smoke failed: {streamed}")
        controller.update_preferences(
            {"/imageformat": "Normal JPEG"},
            {"width_percent": 80, "height_percent": 70},
            False,
        )
        captured = controller.capture("snapshot", "camera-smoke")
        transfer = captured.get("last_transfer") or {}
        if (not Path(str(transfer.get("local_file") or "")).is_file()
                or transfer.get("remote_deleted") is not True):
            raise RuntimeError(f"Camera capture smoke failed: {captured}")
        recording = controller.start_recording(
            {"/moviesize": "720p"},
            {"width_percent": 80, "height_percent": 70},
            False,
        )
        if not recording["recording_active"]:
            raise RuntimeError(f"Camera video smoke did not start: {recording}")
        video = controller.stop_recording()
        video_transfer = video.get("last_transfer") or {}
        if (video["recording_active"]
                or video_transfer.get("action") != "video"
                or not Path(str(video_transfer.get("local_file") or "")).is_file()
                or video_transfer.get("remote_deleted") is not True):
            raise RuntimeError(f"Camera video smoke failed: {video}")
        controller.select_series_target(
            {"series_path": str(series_service.root), "pixel_id": "SMOKE_1_1"},
            "ivl",
        )
        series_capture = controller.capture("snapshot", "before")
        series_transfer = series_capture.get("last_transfer") or {}
        if (not Path(str(series_transfer.get("local_file") or "")).is_file()
                or len(series_service.records) != 1
                or series_service.records[0][1] != "snapshot"
                or not (series_capture.get("series_target") or {}).get("session_dir")):
            raise RuntimeError(f"Camera series smoke failed: {series_capture}")
        measurement = SmokeMeasurement()
        guided = GuidedCameraWorkflow(controller, measurement, measurement)
        guided.prepare("ivl", {
            "target": {"series_path": str(series_service.root), "pixel_id": "SMOKE_1_1"},
        }, create_telemetry=False)
        guided.continue_measurement()
        deadline = time.monotonic() + 3.0
        while guided.snapshot()["status"] != "awaiting_after_photo" and time.monotonic() < deadline:
            time.sleep(0.01)
        guided_result = guided.finish(True)
        if (guided_result["status"] != "completed"
                or not Path(str(guided_result.get("before_photo") or "")).is_file()
                or not Path(str(guided_result.get("video_file") or "")).is_file()
                or not Path(str(guided_result.get("after_photo") or "")).is_file()):
            raise RuntimeError(f"Guided camera smoke failed: {guided_result}")
        stopped = controller.stop_liveview()
        if stopped["liveview_active"]:
            raise RuntimeError("Camera smoke did not stop LiveView.")
        console_write(json.dumps({
            "camera_connected": True,
            "initialized": True,
            "remote_files": len(connected["files"]),
            "frame_sequence": streamed["frame_sequence"],
            "capture_verified": True,
            "remote_cleanup_verified": True,
            "video_verified": True,
            "series_camera_verified": True,
            "guided_camera_verified": True,
            "liveview_stopped": True,
        }, ensure_ascii=False))
    finally:
        controller.shutdown()
        download_root.cleanup()
    return 0


def series_smoke() -> int:
    """Create, queue, close, and reopen a compatible temporary series."""

    logger = configure_logging()
    client_id = "series-smoke-client-0001"
    with tempfile.TemporaryDirectory(prefix="oled-v2-series-smoke-") as folder:
        root = Path(folder)
        with LocalBackend(logger=logger, series_root=root) as backend:
            assert backend.session is not None
            session = backend.session
            headers = {
                SESSION_HEADER: session.token,
                CLIENT_HEADER: client_id,
                "Content-Type": "application/json",
            }

            def send(path: str, payload: dict, method: str = "POST") -> dict:
                request = urllib.request.Request(
                    f"{session.origin}{path}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method=method,
                )
                with urllib.request.urlopen(request, timeout=8.0) as response:
                    return json.loads(response.read().decode("utf-8"))

            created = send(
                "/api/series/create",
                {
                    "root": str(root),
                    "deposition_date": "2026-07-31",
                    "keyword": "packaged-smoke",
                    "series_led_color": "green",
                    "quarter_bases": {"1": "A", "2": "B", "3": "C", "4": "D"},
                    "quarter_descriptions": {
                        "1": "reference",
                        "2": "transport",
                        "3": "emission",
                        "4": "control",
                    },
                },
            )
            active = created.get("active") or {}
            pixels = active.get("pixels") or []
            if len(pixels) != 48:
                raise RuntimeError("Series smoke did not create all 48 pixels.")
            series_path = Path(str(active.get("path") or ""))
            if not (series_path / "series_journal.xlsx").is_file():
                raise RuntimeError("Series smoke did not create series_journal.xlsx.")
            pixel_id = str(pixels[0].get("pixel_id") or "")
            queued = send(
                "/api/series/current/spectrum-priority",
                {"pixel_id": pixel_id, "enabled": True, "scope": "substrate"},
                method="PUT",
            )
            queue_count = int((queued.get("active") or {}).get("metrics", {}).get("spectrum_queue", 0))
            if queue_count != 4:
                raise RuntimeError("Series smoke did not persist the substrate queue.")
            send("/api/series/close", {})
            reopened = send("/api/series/open", {"path": str(series_path)})
            reopened_active = reopened.get("active") or {}
            if int(reopened_active.get("metrics", {}).get("spectrum_queue", 0)) != 4:
                raise RuntimeError("Series smoke lost the queue after reopening.")

            console_write(
                json.dumps(
                    {
                        "status": "completed",
                        "pixels": len(reopened_active.get("pixels") or []),
                        "spectrum_queue": 4,
                        "journal": "series_journal.xlsx",
                        "reopened": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    return 0


def _create_v191_series_fixture(root: Path) -> tuple[Path, bytes, str, str]:
    """Write a small v1.9.1-compatible series without using current managers."""

    from openpyxl import Workbook

    from oled_app.constants import (
        CONFIG_FILE,
        JOURNAL_FILE,
        MEASUREMENT_HEADERS,
        MEASUREMENTS_SHEET,
        PIXEL_HEADERS,
        PIXELS_SHEET,
        QUARTERS_SHEET,
        SERIES_SHEET,
    )

    series = root / "Старая серия v1.9.1"
    series.mkdir(parents=True)
    config = {
        "app_version": "1.9.1",
        "created_at": "2026-07-30 12:00:00",
        "deposition_date": "2026-07-30",
        "keyword": "legacy acceptance",
        "series_led_color": "green",
        "quarter_bases": {"1": "A", "2": "B", "3": "C", "4": "D"},
        "quarter_descriptions": {
            "1": "reference",
            "2": "transport",
            "3": "emission",
            "4": "control",
        },
        "quarter_led_colors": {str(number): "green" for number in range(1, 5)},
        "quarter_names": {"1": "AG", "2": "BG", "3": "CG", "4": "DG"},
    }
    config_bytes = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    (series / CONFIG_FILE).write_bytes(config_bytes)

    pixel_id = "AG1_1_1"
    relative_ivl = (
        "measurements/01_IVL_VAH/2026-07-30/AG1_1_1/1/legacy_ivl.xlsx"
    )
    ivl_path = series / Path(relative_ivl)
    ivl_path.parent.mkdir(parents=True)
    ivl_workbook = Workbook()
    ivl_sheet = ivl_workbook.active
    ivl_sheet.title = "Cycle_1"
    ivl_sheet.append(["Voltage OLED / LED measured (V)", "Current OLED / LED (mA)"])
    ivl_sheet.append([0.0, 0.0])
    ivl_sheet.append([3.0, 1.25])
    ivl_workbook.save(ivl_path)
    ivl_workbook.close()

    journal = Workbook()
    journal.remove(journal.active)
    series_sheet = journal.create_sheet(SERIES_SHEET)
    series_sheet.append(["OLED series journal"])
    series_sheet.append([])
    series_sheet.append(["App version", "1.9.1"])
    series_sheet.append(["Created at", config["created_at"]])
    series_sheet.append(["Deposition date", config["deposition_date"]])
    series_sheet.append(["Keyword", config["keyword"]])

    quarters = journal.create_sheet(QUARTERS_SHEET)
    quarters.append([
        "Quarter number",
        "Quarter code/name",
        "LED color",
        "Short description",
        "Generated pixel prefix example",
    ])
    codes = {1: "AG", 2: "BG", 3: "CG", 4: "DG"}
    descriptions = config["quarter_descriptions"]
    for quarter_number in range(1, 5):
        code = codes[quarter_number]
        quarters.append([
            quarter_number,
            code,
            "Зеленый (G)",
            descriptions[str(quarter_number)],
            f"{code}{quarter_number}_1_1",
        ])

    pixels = journal.create_sheet(PIXELS_SHEET)
    pixels.append(PIXEL_HEADERS)
    for quarter_number in range(1, 5):
        code = codes[quarter_number]
        for substrate_number in range(1, 4):
            for pixel_number in range(1, 5):
                current_id = f"{code}{quarter_number}_{substrate_number}_{pixel_number}"
                measured = current_id == pixel_id
                pixels.append([
                    current_id,
                    code,
                    quarter_number,
                    descriptions[str(quarter_number)],
                    "Зеленый (G)",
                    substrate_number,
                    pixel_number,
                    "WORKING" if measured else "UNKNOWN",
                    2.6 if measured else "",
                    "2026-07-30 12:30:00" if measured else "",
                    relative_ivl if measured else "",
                    1.25 if measured else "",
                    0.75 if measured else "",
                    False,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "2026-07-30 12:30:00" if measured else "",
                ])

    measurements = journal.create_sheet(MEASUREMENTS_SHEET)
    measurements.append(MEASUREMENT_HEADERS)
    measurements.append([
        "2026-07-30 12:30:00",
        "2026-07-30",
        "IVL",
        pixel_id,
        "WORKING",
        relative_ivl,
        '{"source": "v1.9.1"}',
        "legacy measurement",
    ])
    operator_notes = journal.create_sheet("Operator Notes")
    operator_notes["A1"] = "keep this legacy sheet"
    journal.save(series / JOURNAL_FILE)
    journal.close()
    return series, config_bytes, pixel_id, relative_ivl


def legacy_series_smoke() -> int:
    """Open a v1.9.1 series through the packaged API and preserve its data."""

    from openpyxl import load_workbook

    from oled_app.constants import CONFIG_FILE, JOURNAL_FILE, MEASUREMENTS_SHEET, PIXELS_SHEET

    logger = configure_logging()
    client_id = "legacy-series-smoke-client-0001"
    with tempfile.TemporaryDirectory(prefix="oled-v2-legacy-series-smoke-") as folder:
        root = Path(folder) / "Серии OLED"
        root.mkdir()
        series_path, config_before, pixel_id, relative_ivl = _create_v191_series_fixture(root)
        with LocalBackend(logger=logger, series_root=root) as backend:
            assert backend.session is not None
            session = backend.session
            headers = {
                SESSION_HEADER: session.token,
                CLIENT_HEADER: client_id,
                "Content-Type": "application/json",
            }

            def send(path: str, payload: Optional[dict] = None) -> dict:
                request = urllib.request.Request(
                    f"{session.origin}{path}",
                    data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
                    headers=headers,
                    method="POST" if payload is not None else "GET",
                )
                with urllib.request.urlopen(request, timeout=8.0) as response:
                    return json.loads(response.read().decode("utf-8"))

            listed = send("/api/series/state")
            recent = listed.get("recent") or []
            if len(recent) != 1 or recent[0].get("measurements_count") != 1:
                raise RuntimeError("Legacy series was not listed with its measurement history.")
            opened = send("/api/series/open", {"path": str(series_path)})
            active = opened.get("active") or {}
            pixels = active.get("pixels") or []
            measured = next((item for item in pixels if item.get("pixel_id") == pixel_id), None)
            if len(pixels) != 48 or measured is None:
                raise RuntimeError("Legacy series pixel map was not restored.")
            if (
                active.get("keyword") != "legacy acceptance"
                or active.get("series_led_color") != "green"
                or measured.get("status") != "WORKING"
                or measured.get("opening_voltage_V") != 2.6
                or measured.get("last_ivl_file") != relative_ivl
            ):
                raise RuntimeError("Legacy series metadata or IVL result changed while opening.")
            metrics = active.get("metrics") or {}
            if metrics.get("history") != 1 or metrics.get("ivl") != 1:
                raise RuntimeError("Legacy measurement counters were not preserved.")

        if (series_path / CONFIG_FILE).read_bytes() != config_before:
            raise RuntimeError("Opening a legacy series unexpectedly rewrote its config.")
        workbook = load_workbook(series_path / JOURNAL_FILE, data_only=True, read_only=True)
        try:
            if "Operator Notes" not in workbook.sheetnames:
                raise RuntimeError("Opening a legacy series removed an unknown journal sheet.")
            if workbook["Operator Notes"]["A1"].value != "keep this legacy sheet":
                raise RuntimeError("Opening a legacy series changed operator notes.")
            if workbook[MEASUREMENTS_SHEET].max_row != 2:
                raise RuntimeError("Opening a legacy series changed measurement history.")
            headers = [cell.value for cell in workbook[PIXELS_SHEET][1]]
            pixel_column = headers.index("Pixel ID") + 1
            status_column = headers.index("Last status") + 1
            preserved = False
            for row_number in range(2, workbook[PIXELS_SHEET].max_row + 1):
                if workbook[PIXELS_SHEET].cell(row_number, pixel_column).value == pixel_id:
                    preserved = (
                        workbook[PIXELS_SHEET].cell(row_number, status_column).value
                        == "WORKING"
                    )
                    break
            if not preserved:
                raise RuntimeError("Opening a legacy series lost the existing pixel status.")
        finally:
            workbook.close()

        console_write(json.dumps({
            "status": "completed",
            "source_version": "1.9.1",
            "pixels": len(pixels),
            "measurements": 1,
            "config_unchanged": True,
            "custom_sheet_preserved": True,
            "pixel_result_preserved": True,
        }, ensure_ascii=False, indent=2))
    return 0


def report_smoke() -> int:
    """Build a diagnostic XLSX through the packaged report service."""

    from openpyxl import load_workbook
    from .report import ReportService

    logger = configure_logging()
    with tempfile.TemporaryDirectory(prefix="oled-v2-report-smoke-") as folder:
        series = Path(folder) / "series"
        (series / "measurements" / "01_IVL_VAH" / "2026-09-30").mkdir(parents=True)
        service = ReportService(logger=logger)
        service.start(series, {
            "mode": "ivl", "grouping": "settings", "ivl_date": "2026-09-30",
            "spectrum_date": "", "excluded_quarters": [], "format": "xlsx",
            "output_name": "report_smoke.xlsx",
        })
        deadline = time.monotonic() + 10.0
        while service.snapshot()["active"] and time.monotonic() < deadline:
            time.sleep(0.02)
        state = service.snapshot()
        output = series / "report_smoke.xlsx"
        if state["status"] != "completed" or not output.is_file():
            raise RuntimeError(f"Report smoke failed: {state}")
        workbook = load_workbook(output, read_only=True)
        try:
            if "IVL_U_I_PD" not in workbook.sheetnames or "Spectra_by_voltage" in workbook.sheetnames:
                raise RuntimeError("Report smoke workbook has unexpected sheets.")
        finally:
            workbook.close()
        console_write(json.dumps({
            "status": "completed",
            "format": "xlsx",
            "report_api": "ready",
            "workbook_verified": True,
        }, ensure_ascii=False))
    return 0


def recalculation_smoke() -> int:
    """Verify spectral calibration and destructive luminance recalculation."""

    from copy import deepcopy

    from oled_app.settings import DEFAULT_APP_SETTINGS

    from .ivl import IvlController
    from .recalculation import RecalculationService
    from .series_service import SeriesService
    from .spectrum import SpectrumController

    with tempfile.TemporaryDirectory(prefix="oled-v2-recalculation-smoke-") as folder:
        root = Path(folder)
        series_service = SeriesService(root / "series")
        active = series_service.create_series({
            "root": str(root / "series"),
            "deposition_date": "2026-09-30",
            "keyword": "recalculation-smoke",
            "series_led_color": "green",
            "quarter_bases": {str(number): "Q" for number in range(1, 5)},
            "quarter_descriptions": {str(number): "Smoke" for number in range(1, 5)},
        })["active"]
        pixel_id = active["pixels"][0]["pixel_id"]
        target = {"series_path": active["path"], "pixel_id": pixel_id}
        spectrum = SpectrumController(root / "standalone-spectrum", series_service)
        ivl = IvlController(root / "standalone-ivl", series_service)
        settings_box = {"value": deepcopy(DEFAULT_APP_SETTINGS)}
        service = RecalculationService(
            settings_loader=lambda: deepcopy(settings_box["value"]),
            settings_saver=lambda value: settings_box.update(value=deepcopy(value)),
        )
        try:
            spectrum.start({
                "voltage_start": 3.0,
                "voltage_end": 3.1,
                "voltage_step": 0.1,
                "settle_time_voltage_s": 0.0,
                "settle_time_spectrum_s": 0.0,
                "discard_first_scan_after_tint_change": False,
                "target": target,
                "use_opening_voltage": False,
            })
            deadline = time.monotonic() + 25.0
            while spectrum.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.03)
            if spectrum.snapshot()["status"] != "completed":
                raise RuntimeError(f"Recalculation spectrum seed failed: {spectrum.snapshot()}")

            ivl.start({
                "sweep_end": 0.2,
                "sweep_increment": 0.1,
                "target": target,
            })
            deadline = time.monotonic() + 20.0
            while ivl.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.03)
            if ivl.snapshot()["status"] != "completed":
                raise RuntimeError(f"Recalculation IVL seed failed: {ivl.snapshot()}")

            options = service.options(Path(active["path"]))
            group = next(item for item in options["groups"] if item["candidates"])
            service.start_calibration(Path(active["path"]), {
                "selections": {
                    group["key"]: {"pixel_id": pixel_id, "strategy": "replace"}
                },
                "thresholds": {
                    "median_tolerance_percent": 10.0,
                    "linear_model_outlier_percent": 50.0,
                },
            })
            deadline = time.monotonic() + 15.0
            while service.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.03)
            calibration = service.snapshot()
            if calibration["status"] != "completed":
                raise RuntimeError(f"Spectral calibration smoke failed: {calibration}")
            calibration_file = Path(calibration["result"]["items"][0]["output"])
            if not calibration_file.is_file():
                raise RuntimeError("Spectral calibration smoke did not create XLSX.")

            service.start_luminance(Path(active["path"]), {"confirmed": True})
            deadline = time.monotonic() + 15.0
            while service.snapshot()["active"] and time.monotonic() < deadline:
                time.sleep(0.03)
            luminance = service.snapshot()
            result = luminance.get("result") or {}
            if (luminance["status"] != "completed"
                    or int(result.get("workbooks_updated", 0)) < 2
                    or int(result.get("errors", 0)) != 0):
                raise RuntimeError(f"Luminance recalculation smoke failed: {luminance}")
            console_write(json.dumps({
                "status": "completed",
                "spectral_calibration_verified": True,
                "separate_workbook_verified": True,
                "luminance_workbooks_updated": result["workbooks_updated"],
                "raw_files_restored": result["raw_files_restored"],
            }, ensure_ascii=False))
        finally:
            service.shutdown()
            spectrum.shutdown()
            ivl.shutdown()
    return 0


def launch_desktop(auto_close_after_s: Optional[float] = None) -> int:
    dependencies = dependency_status()
    missing = [
        name for name in ("fastapi", "uvicorn", "websockets", "webview", "static_index")
        if not dependencies[name]
    ]
    if missing:
        raise RuntimeError(
            "v2 prototype dependencies are incomplete: "
            + ", ".join(missing)
            + ". Install requirements-v2.txt and build the frontend."
        )
    if not dependencies["webview2_runtime"]:
        message = (
            "Для запуска OLED Measurement App требуется Microsoft Edge WebView2 Runtime.\n\n"
            "Установите Evergreen WebView2 Runtime с сайта Microsoft и повторите запуск."
        )
        if auto_close_after_s is None:
            show_windows_error("OLED Measurement App — не найден WebView2", message)
        console_write(message, error=True)
        return 2

    import webview

    logger = configure_logging()
    backend = LocalBackend(logger=logger)
    session = backend.start()
    logger.info("Opening WebView2 window version=%s", APP_VERSION)
    try:
        window = webview.create_window(
            f"OLED Measurement App {APP_VERSION}",
            session.launch_url,
            width=1280,
            height=800,
            min_size=(1180, 720),
            resizable=True,
            background_color="#eef2f6",
            text_select=True,
        )

        def close_smoke_window() -> None:
            if auto_close_after_s is None:
                return
            time.sleep(max(0.25, float(auto_close_after_s)))
            window.destroy()

        webview.start(
            close_smoke_window if auto_close_after_s is not None else None,
            gui="edgechromium",
            debug=False,
        )
    finally:
        backend.stop()
        logger.info("Desktop window closed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the isolated OLED v2 desktop prototype.")
    parser.add_argument("--status", action="store_true", help="Print dependency and build status.")
    parser.add_argument(
        "--backend-smoke",
        action="store_true",
        help="Start the loopback backend, authenticate one state request, and stop.",
    )
    parser.add_argument(
        "--poc-smoke",
        action="store_true",
        help="Run an authenticated eight-point simulator PoC and verify safe shutdown.",
    )
    parser.add_argument("--ivl-smoke", action="store_true",
                        help="Run a simulator IVL cycle and verify CSV/XLSX and shutdown.")
    parser.add_argument("--spectrum-smoke", action="store_true",
                        help="Run a simulator spectrum and verify T_int, CSV/XLSX and journal.")
    parser.add_argument("--stability-smoke", action="store_true",
                        help="Run simulator stability and verify live setpoint, XLSX and journal.")
    parser.add_argument("--camera-smoke", action="store_true",
                        help="Verify the v2 camera session, remote files and LiveView lifecycle.")
    parser.add_argument(
        "--series-smoke",
        action="store_true",
        help="Create and reopen a temporary compatible series through the authenticated API.",
    )
    parser.add_argument(
        "--legacy-series-smoke",
        action="store_true",
        help="Open a v1.9.1 temporary series and verify that existing data stays intact.",
    )
    parser.add_argument(
        "--report-smoke",
        action="store_true",
        help="Build and verify a diagnostic XLSX through the v2 report service.",
    )
    parser.add_argument(
        "--recalculation-smoke",
        action="store_true",
        help="Verify spectral calibration and series luminance recalculation.",
    )
    parser.add_argument(
        "--diagnostics-smoke",
        action="store_true",
        help="Verify the authenticated, secret-free diagnostics summary.",
    )
    parser.add_argument(
        "--packaging-smoke",
        action="store_true",
        help="Verify writable user data and the installed WebView2 Runtime.",
    )
    parser.add_argument(
        "--window-smoke",
        action="store_true",
        help="Open the WebView2 shell briefly, then close it automatically.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.status:
            for line in status_lines():
                console_write(line)
            return 0
        if args.backend_smoke:
            return backend_smoke()
        if args.poc_smoke:
            return poc_smoke()
        if args.ivl_smoke:
            return ivl_smoke()
        if args.spectrum_smoke:
            return spectrum_smoke()
        if args.stability_smoke:
            return stability_smoke()
        if args.camera_smoke:
            return camera_smoke()
        if args.series_smoke:
            return series_smoke()
        if args.legacy_series_smoke:
            return legacy_series_smoke()
        if args.report_smoke:
            return report_smoke()
        if args.recalculation_smoke:
            return recalculation_smoke()
        if args.diagnostics_smoke:
            return diagnostics_smoke()
        if args.packaging_smoke:
            return packaging_smoke()
        return launch_desktop(auto_close_after_s=1.5 if args.window_smoke else None)
    except Exception as exc:
        console_write(f"Не удалось запустить v2 prototype: {exc}", error=True)
        return 1
