"""Desktop launcher for the v2 technical prototype."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.request
from typing import Iterable, Optional

from oled_app.constants import APP_VERSION

from .config import CLIENT_HEADER, SESSION_HEADER, default_static_root
from .logging_setup import configure_logging, log_directory
from .server import LocalBackend


def dependency_status() -> dict:
    return {
        "fastapi": importlib.util.find_spec("fastapi") is not None,
        "uvicorn": importlib.util.find_spec("uvicorn") is not None,
        "webview": importlib.util.find_spec("webview") is not None,
        "static_index": (default_static_root() / "index.html").is_file(),
    }


def status_lines() -> list[str]:
    dependencies = dependency_status()
    return [
        f"OLED Measurement App v{APP_VERSION} — v2 technical prototype",
        "Stable default launcher: oled_modular_app.py (Tkinter)",
        f"Frontend build: {'ready' if dependencies['static_index'] else 'missing'}",
        f"FastAPI/Uvicorn: {'ready' if dependencies['fastapi'] and dependencies['uvicorn'] else 'missing'}",
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
        # Stage 2 will attach the active SMU emergency coordinator here.
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
    try:
        return launch_desktop(auto_close_after_s=1.5 if args.window_smoke else None)
    except Exception as exc:
        print(f"Не удалось запустить v2 prototype: {exc}", file=sys.stderr)
        return 1
