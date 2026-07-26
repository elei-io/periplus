"""Deployment commands for the repository catalogue."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from repository.catalogue import catalogue_from_env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repository.catalogue")
    parser.add_argument(
        "command",
        choices=("bootstrap", "check"),
        help="Initialize or validate the fixed Atlas catalogue.",
    )
    arguments = parser.parse_args(argv)

    with catalogue_from_env() as catalogue:
        if arguments.command == "bootstrap":
            catalogue.bootstrap()
            print("Atlas catalogue initialized and valid.")
        elif arguments.command == "check":
            catalogue.validate_schema()
            print("Atlas catalogue is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
