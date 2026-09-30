from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from oled_v2.diagnostics import create_diagnostics_snapshot, redact_diagnostic_text


class V2DiagnosticsTests(unittest.TestCase):
    def test_redaction_removes_session_secrets(self) -> None:
        source = (
            "X-OLED-Session: secret-header token=secret-token "
            "client_id=secret-client #session=secret-fragment Authorization=Bearer-secret"
        )

        redacted = redact_diagnostic_text(source)

        for secret in ("secret-header", "secret-token", "secret-client", "secret-fragment", "Bearer-secret"):
            self.assertNotIn(secret, redacted)
        self.assertIn("[скрыто]", redacted)

    def test_snapshot_is_bounded_and_contains_no_log_token(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "oled-v2.log").write_text(
                "INFO normal\n"
                "ERROR request token=super-secret failed\n"
                "WARNING client_id=private-client retry\n",
                encoding="utf-8",
            )
            snapshot = create_diagnostics_snapshot(
                settings={"default_root": str(root / "series")},
                hardware={"mode": "Эмулятор", "smu": "ready", "spectrometer": "ready"},
                series={"active": False, "path": None, "root": str(root / "series")},
                operations={"ВАЯХ": {"status": "failed", "active": False, "error": "token=operation-secret"}},
                camera={"connected": False, "initialized": False, "error": None, "message": "Камера не подключена."},
                settings_path=root / "settings.json",
                log_dir=log_dir,
                backend_ready=True,
                backend_started_at="2026-09-30T10:00:00+00:00",
            )

        serialized = str(snapshot)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("private-client", serialized)
        self.assertNotIn("operation-secret", serialized)
        self.assertIn("OLED Measurement App", snapshot["copy_text"])
        self.assertEqual(snapshot["operations"][0]["status"], "failed")
        self.assertLessEqual(len(snapshot["recent_errors"]), 12)


if __name__ == "__main__":
    unittest.main()
