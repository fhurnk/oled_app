"""FastAPI application for the isolated v2 technical prototype."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from oled_app.constants import APP_VERSION
from oled_app.settings import hardware_mode_label, load_app_settings

from .config import API_SCHEMA_VERSION, SessionConfig
from .logging_setup import log_directory
from .security import ControllerLease, require_controller, require_session


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws:; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_path(config: SessionConfig) -> Path:
    return config.static_root / "index.html"


def create_app(config: SessionConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.started_at = _utc_now()
        app.state.ready = True
        yield
        app.state.ready = False

    app = FastAPI(
        title="OLED Measurement App v2 backend",
        version=APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.session_config = config
    app.state.controller_lease = ControllerLease()
    app.state.started_at = _utc_now()
    app.state.ready = False

    @app.middleware("http")
    async def restrict_local_desktop_requests(request: Request, call_next):
        host = request.headers.get("host", "")
        if host != config.expected_host_header:
            return JSONResponse(
                {"detail": "Unexpected Host header."},
                status_code=403,
                headers=SECURITY_HEADERS,
            )
        origin = request.headers.get("origin")
        if origin and origin != config.origin:
            return JSONResponse(
                {"detail": "Unexpected Origin header."},
                status_code=403,
                headers=SECURITY_HEADERS,
            )
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/api/app/health", dependencies=[Depends(require_session)])
    async def health() -> dict:
        return {
            "ready": bool(app.state.ready),
            "schema_version": API_SCHEMA_VERSION,
            "session_id": config.session_id,
        }

    @app.get("/api/app/state")
    async def app_state(_client_id: str = Depends(require_controller)) -> dict:
        settings = load_app_settings()
        return {
            "schema_version": API_SCHEMA_VERSION,
            "session_id": config.session_id,
            "timestamp": _utc_now(),
            "application": {
                "name": "OLED Measurement App",
                "version": APP_VERSION,
                "channel": "alpha",
                "stable_base": "v1.9.1",
                "shell": "WebView2 technical prototype",
            },
            "backend": {
                "ready": bool(app.state.ready),
                "bound_host": config.host,
                "started_at": app.state.started_at,
                "api_docs_enabled": False,
                "log_directory": str(log_directory()),
            },
            "hardware": {
                "mode": hardware_mode_label(settings),
                "smu": "not_probed",
                "spectrometer": "not_probed",
                "camera": "not_probed",
            },
            "series": {
                "active": False,
                "path": None,
            },
            "migration": {
                "stage": 1,
                "status": "stage_1_complete",
                "tkinter_default_preserved": True,
            },
        }

    index = _index_path(config)
    assets = config.static_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    async def frontend_index():
        if not index.is_file():
            return HTMLResponse(
                "<!doctype html><html lang='ru'><meta charset='utf-8'>"
                "<title>OLED v2 frontend не собран</title>"
                "<body><h1>Frontend v2 не собран</h1>"
                "<p>Запустите scripts/build_v2_frontend.ps1.</p></body></html>",
                status_code=503,
                headers=SECURITY_HEADERS,
            )
        return FileResponse(str(index), media_type="text/html", headers=SECURITY_HEADERS)

    return app
