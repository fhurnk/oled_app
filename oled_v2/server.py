"""Loopback-only Uvicorn lifecycle owned by the desktop launcher."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import uvicorn

from .api import create_app
from .config import SESSION_HEADER, SessionConfig, create_session_config


class BackendStartupError(RuntimeError):
    pass


class LocalBackend:
    def __init__(
        self,
        static_root: Optional[Path] = None,
        logger=None,
        series_root: Optional[Path] = None,
    ) -> None:
        self.static_root = Path(static_root) if static_root is not None else None
        self.logger = logger
        self.series_root = Path(series_root) if series_root is not None else None
        self.session: Optional[SessionConfig] = None
        self.server: Optional[uvicorn.Server] = None
        self.thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

    def start(self, timeout_s: float = 10.0) -> SessionConfig:
        if self.thread is not None:
            raise RuntimeError("The local backend is already running.")

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = int(listener.getsockname()[1])
        session = create_session_config(port=port, static_root=self.static_root)
        app = create_app(
            session,
            logger=self.logger,
            series_root=self.series_root,
        )
        config = uvicorn.Config(
            app,
            host=session.host,
            port=session.port,
            log_level="warning",
            access_log=False,
            lifespan="on",
            ws="websockets-sansio",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="oled-v2-backend",
            daemon=True,
        )
        self._socket = listener
        self.session = session
        self.server = server
        self.thread = thread
        thread.start()
        try:
            self._wait_until_ready(timeout_s)
        except Exception:
            self.stop()
            raise
        if self.logger:
            self.logger.info(
                "Backend ready session=%s origin=%s",
                session.session_id,
                session.origin,
            )
        return session

    def _wait_until_ready(self, timeout_s: float) -> None:
        assert self.session is not None
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self.thread is not None and not self.thread.is_alive():
                raise BackendStartupError("The local backend exited before becoming ready.")
            request = urllib.request.Request(
                f"{self.session.origin}/api/app/health",
                headers={SESSION_HEADER: self.session.token},
            )
            try:
                with urllib.request.urlopen(request, timeout=0.5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if response.status == 200 and payload.get("ready"):
                        return
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise BackendStartupError(f"Timed out waiting for the local backend: {last_error}")

    def stop(self, timeout_s: float = 5.0) -> None:
        server = self.server
        thread = self.thread
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout_s)))
        if thread is not None and thread.is_alive() and server is not None:
            server.force_exit = True
            thread.join(timeout=1.0)
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self.logger and self.session:
            self.logger.info("Backend stopped session=%s", self.session.session_id)
        self.server = None
        self.thread = None
        self._socket = None

    def __enter__(self) -> "LocalBackend":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
