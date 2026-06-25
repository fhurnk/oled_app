#!/usr/bin/env python3
"""Create a GitHub Release for an existing project version."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def normalize_tag(value: str) -> str:
    value = value.strip()
    return value if value.startswith("v") else f"v{value}"


def repo_from_remote_url(url: str) -> Optional[str]:
    text = url.strip()
    patterns = [
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
        r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def infer_repo(remote_name: str) -> Optional[str]:
    for git_dir_name in [".git-local", ".git"]:
        config_path = ROOT / git_dir_name / "config"
        if not config_path.exists():
            continue
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        section = f'remote "{remote_name}"'
        if parser.has_section(section):
            repo = repo_from_remote_url(parser.get(section, "url", fallback=""))
            if repo:
                return repo
    return None


def default_notes_path(tag: str) -> Path:
    return ROOT / "docs" / "versions" / f"{tag}.md"


def run_gh_release(args: argparse.Namespace, notes_path: Path) -> int:
    command = [
        "gh",
        "release",
        "create",
        args.tag,
        "--repo",
        args.repo,
        "--title",
        args.title or args.tag,
        "--notes-file",
        str(notes_path),
    ]
    if args.target:
        command.extend(["--target", args.target])
    if args.draft:
        command.append("--draft")
    if args.prerelease:
        command.append("--prerelease")
    if args.latest:
        command.extend(["--latest", args.latest])
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.call(command)


def create_release_via_api(args: argparse.Namespace, notes: str) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError(
            "No GitHub token found. Set GITHUB_TOKEN or GH_TOKEN with repo contents access, "
            "or install/authenticate GitHub CLI and rerun without --api-only."
        )

    payload = {
        "tag_name": args.tag,
        "name": args.title or args.tag,
        "body": notes,
        "draft": bool(args.draft),
        "prerelease": bool(args.prerelease),
    }
    if args.target:
        payload["target_commitish"] = args.target
    if args.latest:
        payload["make_latest"] = args.latest

    request = urllib.request.Request(
        f"https://api.github.com/repos/{args.repo}/releases",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "oled-app-release-script",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return str(data.get("html_url") or data.get("url") or "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 422 and "already_exists" in detail:
            return f"https://github.com/{args.repo}/releases/tag/{args.tag}"
        raise RuntimeError(f"GitHub API failed with HTTP {exc.code}: {detail}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a GitHub Release from docs/versions/vX.Y.Z.md.")
    parser.add_argument("tag", help="Release tag, for example v1.5.5 or 1.5.5.")
    parser.add_argument("--repo", help="GitHub repository in owner/name form. Defaults to remote URL.")
    parser.add_argument("--remote", default="oled_app", help="Remote name used to infer --repo.")
    parser.add_argument("--notes", help="Release notes file. Defaults to docs/versions/<tag>.md.")
    parser.add_argument("--title", help="Release title. Defaults to the tag.")
    parser.add_argument("--target", help="Target branch or commit for a new tag.")
    parser.add_argument("--draft", action="store_true", help="Create a draft release.")
    parser.add_argument("--prerelease", action="store_true", help="Mark release as prerelease.")
    parser.add_argument("--latest", choices=["true", "false", "legacy"], help="GitHub make_latest value.")
    parser.add_argument("--api-only", action="store_true", help="Skip gh CLI and use GitHub REST API.")
    parser.add_argument("--dry-run", action="store_true", help="Print the gh command or API payload intent.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.tag = normalize_tag(args.tag)
    args.repo = args.repo or infer_repo(args.remote)
    if not args.repo:
        raise SystemExit("Could not infer repository. Pass --repo owner/name.")

    notes_path = Path(args.notes) if args.notes else default_notes_path(args.tag)
    if not notes_path.is_absolute():
        notes_path = ROOT / notes_path
    if not notes_path.exists():
        raise SystemExit(f"Release notes file not found: {notes_path}")

    if not args.api_only and shutil.which("gh"):
        return run_gh_release(args, notes_path)

    notes = notes_path.read_text(encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"repo": args.repo, "tag": args.tag, "notes": str(notes_path)}, ensure_ascii=False, indent=2))
        return 0
    url = create_release_via_api(args, notes)
    print(f"Created GitHub Release: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
