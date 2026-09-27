"""Desktop launcher for the v2 technical prototype."""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def dependency_status() -> dict:
    return {
        "fastapi": importlib.util.find_spec("fastapi") is not None,
        "uvicorn": importlib.util.find_spec("uvicorn") is not None,
        "websockets": importlib.util.find_spec("websockets") is not None,
        "webview": importlib.util.find_spec("webview") is not None,
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
        f"Logs: {log_directory()}",
    ]


def backend_smoke() -> int:
    logger = configure_logging()
    with LocalBackend(logger=logger) as backend:
        assert backend.session is not None
        request = urllib.request.Request(
            f"{backend.session.origin}/api/app/state",
            headers={
                SESSION_HEADER: backend.session.token,
                CLIENT_HEADER: "backend-smoke-client-0001",
            },
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("application", {}).get("version") != APP_VERSION:
            raise RuntimeError("Backend version does not match APP_VERSION.")
        console_write(
            json.dumps(
                {
                    "ready": payload["backend"]["ready"],
                    "version": payload["application"]["version"],
                    "origin": backend.session.origin,
                    "session_id": backend.session.session_id,
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
    """Exercise the backend-owned camera session and cancellable frame stream."""
    import threading

    from oled_app.camera.client import RemoteFile
    from .camera import CameraController

    class SmokeCameraClient:
        def __init__(self, base_url: str, timeout_s: float, stream_timeout_s: float):
            self.base_url = base_url
            self.stop_calls = 0

        def health(self):
            return {"status": "ok", "service": "smoke-camera"}

        def initialize(self):
            return {"success": True}

        def status(self):
            return {"model": "Smoke Canon"}

        def capabilities(self):
            return {"video_settings": {"resolution": ["640x480"]}}

        def list_files(self):
            return [RemoteFile("smoke-1", "smoke.jpg", "photo", 16)]

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

    controller = CameraController(client_factory=SmokeCameraClient)
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
        stopped = controller.stop_liveview()
        if stopped["liveview_active"]:
            raise RuntimeError("Camera smoke did not stop LiveView.")
        console_write(json.dumps({
            "camera_connected": True,
            "initialized": True,
            "remote_files": len(connected["files"]),
            "frame_sequence": streamed["frame_sequence"],
            "liveview_stopped": True,
        }, ensure_ascii=False))
    finally:
        controller.shutdown()
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


def launch_desktop(auto_close_after_s: Optional[float] = None) -> int:
    missing = [name for name, present in dependency_status().items() if not present]
    if missing:
        raise RuntimeError(
            "v2 prototype dependencies are incomplete: "
            + ", ".join(missing)
            + ". Install requirements-v2.txt and build the frontend."
        )

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
        "--window-smoke",
        action="store_true",
        help="Open the WebView2 shell briefly, then close it automatically.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
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
    try:
        return launch_desktop(auto_close_after_s=1.5 if args.window_smoke else None)
    except Exception as exc:
        console_write(f"Не удалось запустить v2 prototype: {exc}", error=True)
        return 1
