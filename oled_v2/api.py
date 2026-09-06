"""FastAPI application for the isolated v2 technical prototype."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from oled_app.constants import APP_VERSION
from oled_app.settings import load_app_settings

from .config import API_SCHEMA_VERSION, SessionConfig
from .logging_setup import log_directory
from .poc import PocBusyError, PocController
from .ivl import IvlController
from .security import (
    WS_APP_PROTOCOL,
    ControllerLease,
    authenticate_websocket,
    require_controller,
    require_session,
)
from .series_service import (
    SeriesConflictError,
    SeriesNotFoundError,
    SeriesService,
    SeriesServiceError,
    SeriesValidationError,
)


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


def create_app(
    config: SessionConfig,
    logger=None,
    series_root: Optional[Path] = None,
) -> FastAPI:
    poc_controller = PocController(logger=logger)
    ivl_controller = IvlController()
    operation_gate = asyncio.Lock()
    series_service = SeriesService(default_root=series_root, logger=logger)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.started_at = _utc_now()
        app.state.ready = True
        try:
            yield
        finally:
            await asyncio.to_thread(ivl_controller.shutdown)
            await asyncio.to_thread(poc_controller.shutdown)
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
    app.state.ivl_controller = ivl_controller
    app.state.poc_controller = poc_controller
    app.state.series_service = series_service
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
        hardware = poc_controller.hardware_summary(settings)
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
            "hardware": hardware,
            "series": series_service.app_summary(),
            "migration": {
                "stage": 5,
                "status": "stage_5_simulator_ivl_in_progress",
                "tkinter_default_preserved": True,
            },
        }

    def series_http_error(exc: SeriesServiceError) -> HTTPException:
        if isinstance(exc, SeriesNotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, SeriesConflictError):
            code = status.HTTP_409_CONFLICT
        elif isinstance(exc, SeriesValidationError):
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            code = status.HTTP_400_BAD_REQUEST
        return HTTPException(status_code=code, detail=str(exc))

    @app.get("/api/series/state")
    async def series_state(_client_id: str = Depends(require_controller)) -> dict:
        return await asyncio.to_thread(series_service.state)

    @app.put("/api/series/root")
    async def series_root_update(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        try:
            return await asyncio.to_thread(series_service.set_root, (payload or {}).get("path"))
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.post("/api/series/open")
    async def series_open(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        try:
            return await asyncio.to_thread(series_service.open_series, (payload or {}).get("path"))
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.post("/api/series/close")
    async def series_close(_client_id: str = Depends(require_controller)) -> dict:
        return await asyncio.to_thread(series_service.close_series)

    @app.post("/api/series/create", status_code=status.HTTP_201_CREATED)
    async def series_create(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        try:
            return await asyncio.to_thread(series_service.create_series, payload or {})
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.put("/api/series/current")
    async def series_update(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        try:
            return await asyncio.to_thread(series_service.update_active, payload or {})
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.post("/api/series/current/refresh")
    async def series_refresh(_client_id: str = Depends(require_controller)) -> dict:
        try:
            return await asyncio.to_thread(series_service.refresh_active)
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.put("/api/series/current/spectrum-priority")
    async def series_spectrum_priority(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        values = payload or {}
        try:
            return await asyncio.to_thread(
                series_service.set_spectrum_priority,
                values.get("pixel_id"),
                values.get("enabled"),
                values.get("scope", "pixel"),
            )
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc

    @app.get("/api/series/current/thumbnail/{pixel_id}")
    async def series_thumbnail(
        pixel_id: str,
        _client_id: str = Depends(require_controller),
    ):
        try:
            thumbnail = await asyncio.to_thread(series_service.thumbnail_for_pixel, pixel_id)
        except SeriesServiceError as exc:
            raise series_http_error(exc) from exc
        return FileResponse(str(thumbnail), media_type="image/png", headers=SECURITY_HEADERS)

    def require_hardware_idle():
        if ivl_controller.snapshot()["active"] or poc_controller.snapshot(False)["active"]:
            raise HTTPException(status_code=409, detail="Дождитесь завершения текущей операции.")

    @app.get("/api/ivl/state")
    async def ivl_state(_client_id: str = Depends(require_controller)) -> dict:
        return ivl_controller.snapshot()

    @app.post("/api/ivl/preflight")
    async def ivl_preflight(payload: dict = Body(...),
                            _client_id: str = Depends(require_controller)) -> dict:
        try:
            return ivl_controller.preflight(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/ivl/start", status_code=202)
    async def ivl_start(payload: dict = Body(...),
                        _client_id: str = Depends(require_controller)) -> dict:
        async with operation_gate:
            require_hardware_idle()
            try:
                return ivl_controller.start(payload)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/ivl/stop")
    async def ivl_stop(_client_id: str = Depends(require_controller)) -> dict:
        return ivl_controller.stop()

    @app.get("/api/poc/state")
    async def poc_state(_client_id: str = Depends(require_controller)) -> dict:
        return poc_controller.snapshot(include_points=True)

    @app.post("/api/poc/probe")
    async def poc_probe(_client_id: str = Depends(require_controller)) -> dict:
        try:
            async with operation_gate:
                require_hardware_idle()
                return await asyncio.to_thread(poc_controller.probe_current_hardware)
        except PocBusyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post("/api/poc/start", status_code=status.HTTP_202_ACCEPTED)
    async def poc_start(
        payload: Optional[dict] = Body(default=None),
        _client_id: str = Depends(require_controller),
    ) -> dict:
        values = payload or {}
        try:
            point_count = int(values.get("point_count", 32))
            interval_ms = float(values.get("interval_ms", 80.0))
            async with operation_gate:
                require_hardware_idle()
                return poc_controller.start_simulator(
                    point_count=point_count,
                    interval_s=interval_ms / 1000.0,
                )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Некорректные параметры PoC: {exc}",
            ) from exc
        except PocBusyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @app.post("/api/poc/stop")
    async def poc_stop(_client_id: str = Depends(require_controller)) -> dict:
        return await asyncio.to_thread(poc_controller.stop_and_wait, "operator", 4.0)

    @app.websocket("/api/poc/stream")
    async def poc_stream(websocket: WebSocket) -> None:
        try:
            authenticate_websocket(websocket)
        except HTTPException as exc:
            await websocket.close(code=4403 if exc.status_code == 403 else 4401)
            return

        await websocket.accept(subprotocol=WS_APP_PROTOCOL)
        state = poc_controller.snapshot(include_points=True)
        cursor = int(state.get("last_event_sequence", 0))
        try:
            await websocket.send_json(
                {
                    "sequence": cursor,
                    "type": "poc_snapshot",
                    "timestamp": _utc_now(),
                    "state": state,
                }
            )
            while True:
                events = await asyncio.to_thread(poc_controller.events_after, cursor, 1.0)
                if not events:
                    await websocket.send_json(
                        {
                            "sequence": cursor,
                            "type": "poc_heartbeat",
                            "timestamp": _utc_now(),
                        }
                    )
                    continue
                for event in events:
                    await websocket.send_json(event)
                    cursor = max(cursor, int(event["sequence"]))
        except WebSocketDisconnect:
            return

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
