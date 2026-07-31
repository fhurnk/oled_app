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
        print(
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

        print(
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

            print(
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
            print(line)
        return 0
    if args.backend_smoke:
        return backend_smoke()
    if args.poc_smoke:
        return poc_smoke()
    if args.series_smoke:
        return series_smoke()
    try:
        return launch_desktop(auto_close_after_s=1.5 if args.window_smoke else None)
    except Exception as exc:
        print(f"Не удалось запустить v2 prototype: {exc}", file=sys.stderr)
        return 1
