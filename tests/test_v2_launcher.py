from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from oled_v2.launcher import status_lines
from oled_v2.logging_setup import log_directory, remove_expired_logs


class V2LauncherTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
