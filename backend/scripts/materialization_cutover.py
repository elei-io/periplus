"""Run append-only materialization cutover checks."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

from atlas.ingestion.objects.config import object_store_from_env
from atlas.ingestion.objects.document import ExactDocumentRepository
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.materialization.cutover import (
    finalize_cutover,
    preflight,
    verify_cutover,
    write_report,
)
from atlas.platform.catalogue import catalogue_from_env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("preflight", "verify", "finalize"),
    )
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()

    with catalogue_from_env(threads=2, memory_limit="2GB") as catalogue:
        if arguments.operation == "preflight":
            store = object_store_from_env()
            report = preflight(
                catalogue,
                RawHtmlRepository(store),
                ExactDocumentRepository(store),
            )
            path = arguments.report or (
                Path(catalogue.config.data_path).resolve().parent
                / "cutovers"
                / (
                    datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                    + "-preflight.json"
                )
            )
            write_report(report, path)
            print(json.dumps({"report": str(path), **asdict(report)}))
        elif arguments.operation == "verify":
            print(json.dumps(verify_cutover(catalogue), sort_keys=True))
        else:
            print(json.dumps(finalize_cutover(catalogue), sort_keys=True))


if __name__ == "__main__":
    main()
