from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from oled_app.constants import APP_VERSION
from oled_v2.config import CLIENT_HEADER, SESSION_HEADER, create_session_config
from oled_v2.security import ControllerLease
from oled_v2.server import LocalBackend


class V2SessionConfigTests(unittest.TestCase):
    def test_token_is_passed_in_fragment_not_http_request_target(self) -> None:
        session = create_session_config(port=54321, token="secret-token")
        parsed = urlsplit(session.launch_url)

        self.assertEqual(parsed.path, "/")
        self.assertEqual(parsed.query, "")
        self.assertEqual(parsed.fragment, "session=secret-token")


class ControllerLeaseTests(unittest.TestCase):
    def test_single_client_can_reclaim_its_lease(self) -> None:
        lease = ControllerLease()

        lease.claim("client-identifier-0001")
        lease.claim("client-identifier-0001")

        self.assertEqual(lease.client_id, "client-identifier-0001")

    def test_second_client_is_rejected(self) -> None:
        lease = ControllerLease()
        lease.claim("client-identifier-0001")

        with self.assertRaisesRegex(Exception, "already has a controlling client"):
            lease.claim("client-identifier-0002")


class V2LoopbackBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        static_root = Path(self.temp_dir.name)
        (static_root / "assets").mkdir()
        (static_root / "index.html").write_text(
            "<!doctype html><html><body><div id='root'>OLED v2 test</div></body></html>",
            encoding="utf-8",
        )
        self.backend = LocalBackend(static_root=static_root)
        self.session = self.backend.start()

    def tearDown(self) -> None:
        self.backend.stop()
        self.temp_dir.cleanup()

    def request(self, path: str, headers=None):
        return urllib.request.urlopen(
            urllib.request.Request(
                f"{self.session.origin}{path}",
                headers=headers or {},
            ),
            timeout=3.0,
        )

    def test_health_requires_session_token(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("/api/app/health")

        self.assertEqual(raised.exception.code, 401)

    def test_wrong_origin_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "/api/app/health",
                {
                    SESSION_HEADER: self.session.token,
                    "Origin": "http://localhost:9999",
                },
            )

        self.assertEqual(raised.exception.code, 403)

    def test_state_returns_version_and_claims_one_controller(self) -> None:
        headers = {
            SESSION_HEADER: self.session.token,
            CLIENT_HEADER: "test-controller-client-0001",
        }
        with self.request("/api/app/state", headers) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["application"]["version"], APP_VERSION)
        self.assertEqual(payload["backend"]["bound_host"], "127.0.0.1")
        self.assertEqual(payload["migration"]["status"], "stage_1_complete")
        self.assertTrue(payload["migration"]["tkinter_default_preserved"])

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "/api/app/state",
                {
                    SESSION_HEADER: self.session.token,
                    CLIENT_HEADER: "second-controller-client-002",
                },
            )
        self.assertEqual(raised.exception.code, 409)

    def test_frontend_is_served_with_security_headers(self) -> None:
        with self.request("/") as response:
            body = response.read().decode("utf-8")

        self.assertIn("OLED v2 test", body)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
