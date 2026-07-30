from __future__ import annotations

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

from scripts.create_github_release import (
    build_gh_release_command,
    normalize_tag,
    run_gh_release,
)


def release_args(**overrides) -> Namespace:
    values = {
        "tag": "v2.0.0-alpha",
        "repo": "fhurnk/oled_app",
        "title": None,
        "target": None,
        "draft": False,
        "prerelease": True,
        "latest": "false",
        "dry_run": False,
        "update_existing": True,
    }
    values.update(overrides)
    return Namespace(**values)


class GitHubReleaseScriptTests(unittest.TestCase):
    def test_normalize_tag_keeps_prerelease_suffix(self) -> None:
        self.assertEqual(normalize_tag("2.0.0-alpha"), "v2.0.0-alpha")

    def test_edit_command_keeps_prerelease_and_latest_flags(self) -> None:
        command = build_gh_release_command(
            release_args(),
            Path("docs/versions/v2.0.0-alpha.md"),
            "edit",
        )

        self.assertEqual(command[:4], ["gh", "release", "edit", "v2.0.0-alpha"])
        self.assertIn("--prerelease", command)
        self.assertEqual(command[-1], "--latest=false")

    def test_update_dry_run_prints_create_and_edit_paths_without_probe(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = run_gh_release(
                release_args(dry_run=True),
                Path("docs/versions/v2.0.0-alpha.md"),
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Existing release: gh release edit", output.getvalue())
        self.assertIn("Missing release: gh release create", output.getvalue())


if __name__ == "__main__":
    unittest.main()
