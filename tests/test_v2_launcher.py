from __future__ import annotations

import os
import io
import json
import logging
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from oled_v2.launcher import (
    console_write,
    main,
    recalculation_smoke,
    report_smoke,
    series_smoke,
    status_lines,
)
from oled_v2.logging_setup import log_directory, remove_expired_logs


class V2LauncherTests(unittest.TestCase):
    def test_cli_failure_returns_code_without_unhandled_windowed_exception(self) -> None:
        with patch("oled_v2.launcher.status_lines", side_effect=RuntimeError("smoke failed")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--status"]), 1)

    def test_windowed_build_can_finish_cli_action_without_stdout(self) -> None:
        with patch("oled_v2.launcher.sys.stdout", None):
            console_write("completed")
        detached = Mock()
        detached.write.side_effect = OSError("detached")
        with patch("oled_v2.launcher.sys.stdout", detached):
            console_write("completed")

    def test_status_keeps_tkinter_as_stable_default(self) -> None:
        self.assertIn(
            "Stable default launcher: oled_modular_app.py (Tkinter)",
            status_lines(),
        )

    def test_log_directory_can_be_redirected_for_tests_and_portable_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"OLED_V2_LOG_DIR": folder}):
                self.assertEqual(log_directory(), Path(folder))

    def test_expired_log_cleanup_keeps_non_log_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_log = root / "old.log"
            old_log.write_text("old", encoding="utf-8")
            other = root / "keep.txt"
            other.write_text("keep", encoding="utf-8")
            expired = time.time() - (31 * 24 * 60 * 60)
            os.utime(old_log, (expired, expired))

            remove_expired_logs(root, retention_days=30)

            self.assertFalse(old_log.exists())
            self.assertTrue(other.exists())

    def test_series_smoke_creates_and_reopens_compatible_journal(self) -> None:
        output = io.StringIO()
        logger = logging.getLogger("oled-v2-series-smoke-test")
        with patch("oled_v2.launcher.configure_logging", return_value=logger):
            with redirect_stdout(output):
                self.assertEqual(series_smoke(), 0)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["pixels"], 48)
        self.assertEqual(payload["spectrum_queue"], 4)
        self.assertTrue(payload["reopened"])

    def test_report_smoke_builds_diagnostic_workbook(self) -> None:
        output = io.StringIO()
        logger = logging.getLogger("oled-v2-report-smoke-test")
        with patch("oled_v2.launcher.configure_logging", return_value=logger):
            with redirect_stdout(output):
                self.assertEqual(report_smoke(), 0)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["workbook_verified"])

    def test_recalculation_smoke_runs_both_operations(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(recalculation_smoke(), 0)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["spectral_calibration_verified"])
        self.assertGreaterEqual(payload["luminance_workbooks_updated"], 2)


if __name__ == "__main__":
    unittest.main()
