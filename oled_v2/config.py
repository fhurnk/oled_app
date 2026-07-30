"""Runtime configuration for the v2 desktop prototype."""

from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4


LOOPBACK_HOST = "127.0.0.1"
API_SCHEMA_VERSION = 1
SESSION_HEADER = "X-OLED-Session"
CLIENT_HEADER = "X-OLED-Client"


def bundled_root() -> Path:
    """Return the PyInstaller extraction root or the repository root."""

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def default_static_root() -> Path:
    return bundled_root() / "oled_v2" / "static"


@dataclass(frozen=True)
class SessionConfig:
    host: str
    port: int
    token: str
    session_id: str
    static_root: Path

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def expected_host_header(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def launch_url(self) -> str:
        # A URL fragment is not sent to the HTTP server or written to access logs.
        return f"{self.origin}/#session={self.token}"


def create_session_config(
    port: int,
    static_root: Optional[Path] = None,
    token: Optional[str] = None,
    session_id: Optional[str] = None,
) -> SessionConfig:
    return SessionConfig(
        host=LOOPBACK_HOST,
        port=int(port),
        token=token or secrets.token_urlsafe(32),
        session_id=session_id or str(uuid4()),
        static_root=Path(static_root) if static_root is not None else default_static_root(),
    )
