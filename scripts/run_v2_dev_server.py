"""Run the v2 loopback backend long enough for local browser inspection."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional, Sequence

from oled_v2.logging_setup import configure_logging
from oled_v2.server import LocalBackend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--info-file",
        type=Path,
        required=True,
        help="Ignored local JSON file that receives the one-time launch URL.",
    )
    parser.add_argument(
        "--lifetime",
        type=float,
        default=90.0,
        help="Maximum server lifetime in seconds (default: 90).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    info_file = args.info_file.resolve()
    info_file.parent.mkdir(parents=True, exist_ok=True)

    logger = configure_logging()
    with LocalBackend(logger=logger) as backend:
        assert backend.session is not None
        payload = {
            "origin": backend.session.origin,
            "launch_url": backend.session.launch_url,
            "session_id": backend.session.session_id,
        }
        info_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        deadline = time.monotonic() + max(1.0, float(args.lifetime))
        while time.monotonic() < deadline:
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
