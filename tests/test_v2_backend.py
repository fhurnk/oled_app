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
        self.series_root = static_root / "series-root"
        self.series_root.mkdir()
        self.backend = LocalBackend(static_root=static_root, series_root=self.series_root)
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
        self.assertEqual(payload["migration"]["status"], "stage_5_simulator_ivl_in_progress")
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

    def test_series_api_creates_updates_queue_and_closes_series(self) -> None:
        headers = {
            SESSION_HEADER: self.session.token,
            CLIENT_HEADER: "series-controller-client-0001",
            "Content-Type": "application/json",
        }
        payload = {
            "root": str(self.series_root),
            "deposition_date": "2026-07-31",
            "keyword": "api",
            "series_led_color": "blue",
            "quarter_bases": {"1": "A", "2": "B", "3": "C", "4": "D"},
            "quarter_descriptions": {"1": "one", "2": "two", "3": "three", "4": "four"},
        }
        create_request = urllib.request.Request(
            f"{self.session.origin}/api/series/create",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(create_request, timeout=5.0) as response:
            created = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 201)
        self.assertEqual(len(created["active"]["pixels"]), 48)

        pixel_id = created["active"]["pixels"][0]["pixel_id"]
        queue_request = urllib.request.Request(
            f"{self.session.origin}/api/series/current/spectrum-priority",
            data=json.dumps({"pixel_id": pixel_id, "enabled": True}).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        with urllib.request.urlopen(queue_request, timeout=5.0) as response:
            queued = json.loads(response.read().decode("utf-8"))
        self.assertEqual(queued["active"]["metrics"]["spectrum_queue"], 1)

        with self.request("/api/app/state", headers) as response:
            app_state = json.loads(response.read().decode("utf-8"))
        self.assertTrue(app_state["series"]["active"])
        self.assertEqual(app_state["series"]["path"], created["active"]["path"])

        close_request = urllib.request.Request(
            f"{self.session.origin}/api/series/close",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(close_request, timeout=5.0) as response:
            closed = json.loads(response.read().decode("utf-8"))
        self.assertIsNone(closed["active"])


if __name__ == "__main__":
    unittest.main()
