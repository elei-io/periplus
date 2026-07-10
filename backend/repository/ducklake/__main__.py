"""Internal deployment commands for the repository DuckLake."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from repository.ducklake.benchmark import print_hot_path_benchmark
from repository.ducklake.client import Catalogue
from repository.ducklake.config import catalogue_config_from_env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repository.ducklake")
    parser.add_argument(
        "command",
        choices=("bootstrap", "check", "benchmark"),
        help="Initialize, validate, or benchmark an existing catalogue.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=100,
        help="Number of existing document/crawl identities to benchmark.",
    )
    arguments = parser.parse_args(argv)

    with Catalogue(catalogue_config_from_env()) as catalogue:
        if arguments.command == "bootstrap":
            catalogue.bootstrap()
            print("Atlas catalogue initialized and valid.")
        elif arguments.command == "check":
            catalogue.validate_schema()
            print("Atlas catalogue is valid.")
        else:
            catalogue.validate_schema()
            print_hot_path_benchmark(catalogue, samples=arguments.samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
