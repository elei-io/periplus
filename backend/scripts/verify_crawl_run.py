"""Wait for one graph run's crawl and DOM evidence in managed DuckLake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from uuid import UUID

from dotenv import load_dotenv


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("--env-file", type=Path, default=Path("../.env"))
    parser.add_argument("--timeout-seconds", type=float, default=90)
    arguments = parser.parse_args()
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    load_dotenv(arguments.env_file)

    from repository.catalogue import catalogue_from_env

    catalogue = catalogue_from_env()
    try:
        deadline = time.monotonic() + arguments.timeout_seconds
        rows: list[tuple] = []
        while time.monotonic() < deadline:
            rows = catalogue.trusted_remote_rows(
                f"""
                SELECT c.crawl_id,
                       c.document_id,
                       c.outcome,
                       c.status_code,
                       d.element_count,
                       (
                           SELECT count(*)
                           FROM main.elements AS e
                           WHERE e.document_id = c.document_id
                       ) AS stored_elements,
                       d.object_key
                FROM main.crawls AS c
                LEFT JOIN main.documents AS d USING (document_id)
                WHERE c.graph_run_id = UUID '{arguments.run_id}'
                """
            )
            if rows:
                break
            time.sleep(1)
        if not rows:
            raise SystemExit(
                "remote ingestion did not become visible before timeout"
            )
        print(
            json.dumps(
                {
                    "run_id": str(arguments.run_id),
                    "rows": [list(row) for row in rows],
                    "latest_snapshot": catalogue.latest_snapshot(),
                },
                default=str,
                indent=2,
            )
        )
    finally:
        catalogue.close()


if __name__ == "__main__":
    main()
