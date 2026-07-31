"""Session authentication and single-controller enforcement."""

from __future__ import annotations

import hmac
import re
from threading import Lock
from typing import Optional

from fastapi import Header, HTTPException, Request, WebSocket, status

from .config import CLIENT_HEADER, SESSION_HEADER, SessionConfig


CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
WS_APP_PROTOCOL = "oled-v2"
WS_SESSION_PREFIX = "oled-session."
WS_CLIENT_PREFIX = "oled-client."


class ControllerLease:
    """Allow exactly one browser client to control a desktop session."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._client_id: Optional[str] = None

    @property
    def client_id(self) -> Optional[str]:
        with self._lock:
            return self._client_id

    def claim(self, client_id: str) -> None:
        if not CLIENT_ID_PATTERN.fullmatch(client_id or ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid controlling client identifier.",
            )
        with self._lock:
            if self._client_id is None:
                self._client_id = client_id
                return
            if not hmac.compare_digest(self._client_id, client_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This desktop session already has a controlling client.",
                )

    def release(self, client_id: str) -> None:
        with self._lock:
            if self._client_id and hmac.compare_digest(self._client_id, client_id):
                self._client_id = None


def validate_session_token(config: SessionConfig, supplied: Optional[str]) -> None:
    if not supplied or not hmac.compare_digest(config.token, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing desktop session token.",
        )


def require_session(request: Request, session_token: Optional[str] = Header(default=None, alias=SESSION_HEADER)) -> None:
    validate_session_token(request.app.state.session_config, session_token)


def require_controller(
    request: Request,
    session_token: Optional[str] = Header(default=None, alias=SESSION_HEADER),
    client_id: Optional[str] = Header(default=None, alias=CLIENT_HEADER),
) -> str:
    validate_session_token(request.app.state.session_config, session_token)
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {CLIENT_HEADER} header.",
        )
    request.app.state.controller_lease.claim(client_id)
    return client_id


def authenticate_websocket(websocket: WebSocket) -> str:
    """Validate local origin plus token/client values carried as subprotocols."""

    config: SessionConfig = websocket.app.state.session_config
    if websocket.headers.get("host", "") != config.expected_host_header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unexpected Host header.")
    origin = websocket.headers.get("origin")
    if origin != config.origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unexpected WebSocket Origin header.",
        )

    protocols = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    if WS_APP_PROTOCOL not in protocols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing v2 WebSocket protocol.",
        )
    session_value = next(
        (item[len(WS_SESSION_PREFIX):] for item in protocols if item.startswith(WS_SESSION_PREFIX)),
        None,
    )
    client_id = next(
        (item[len(WS_CLIENT_PREFIX):] for item in protocols if item.startswith(WS_CLIENT_PREFIX)),
        None,
    )
    validate_session_token(config, session_value)
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {WS_CLIENT_PREFIX} WebSocket protocol.",
        )
    websocket.app.state.controller_lease.claim(client_id)
    return client_id
