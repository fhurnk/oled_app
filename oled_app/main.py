"""Package entrypoint while the legacy monolith is being split."""

from __future__ import annotations


def main() -> None:
    from oled_measurement_app_v2_5 import main as legacy_main

    legacy_main()


if __name__ == "__main__":
    main()
