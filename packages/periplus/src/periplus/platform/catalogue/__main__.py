"""Deployment commands for the repository catalogue."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from periplus.platform.catalogue import catalogue_from_env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m periplus.platform.catalogue")
    parser.add_argument(
        "command",
        choices=("bootstrap", "check"),
        help="Initialize or validate the fixed Periplus catalogue.",
    )
    arguments = parser.parse_args(argv)

    checking = arguments.command == "check"
    with catalogue_from_env(
        read_only=checking,
        override_data_path=checking,
    ) as catalogue:
        if arguments.command == "bootstrap":
            catalogue.bootstrap()
            print("Periplus catalogue initialized and valid.")
        elif arguments.command == "check":
            catalogue.validate_schema()
            print("Periplus catalogue is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
