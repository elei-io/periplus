"""Temporary benchmark for the web.page hostname optimization experiment."""

from __future__ import annotations

import json
from statistics import median
from time import perf_counter

from atlas.platform.catalogue.config import catalogue_config_from_env
from atlas.platform.catalogue.connection import DuckLakeConnectionFactory


QUERY = """
SELECT
    hostname,
    count(*) AS page_count,
    max(last_visited_at) AS most_recent_visit
FROM web.page
WHERE hostname IN (
    'docs.python.org',
    'developer.mozilla.org',
    'commoncrawl.org'
)
GROUP BY hostname
ORDER BY page_count DESC
"""


def main() -> None:
    connection = DuckLakeConnectionFactory(
        catalogue_config_from_env()
    ).connect(read_only=True, override_data_path=True)
    try:
        connection.execute("USE atlas")
        started = perf_counter()
        result = connection.execute(QUERY).fetchall()
        elapsed_ms = (perf_counter() - started) * 1000
        print(f"normal_ms={elapsed_ms:.3f}")
        print(f"result={result!r}")
        connection.execute("SET disabled_optimizers = 'extension'")
        portable = connection.execute(QUERY).fetchall()
        connection.execute("SET disabled_optimizers = ''")
        print(f"portable_equal={result == portable}")
        for run in range(1, 3):
            profile = json.loads(
                connection.execute(
                    "EXPLAIN (ANALYZE, FORMAT JSON) " + QUERY
                ).fetchone()[1]
            )
            print(
                f"warm_run={run} latency_ms={profile['latency'] * 1000:.3f} "
                f"rows_scanned={profile['cumulative_rows_scanned']}"
            )
            print_operators(profile)
        optimized_times: list[float] = []
        portable_times: list[float] = []
        for run in range(6):
            modes = ("portable", "optimized") if run % 2 == 0 else ("optimized", "portable")
            for mode in modes:
                disabled = "extension" if mode == "portable" else ""
                connection.execute(f"SET disabled_optimizers = '{disabled}'")
                profile = json.loads(
                    connection.execute(
                        "EXPLAIN (ANALYZE, FORMAT JSON) " + QUERY
                    ).fetchone()[1]
                )
                elapsed = profile["latency"] * 1000
                (portable_times if mode == "portable" else optimized_times).append(elapsed)
        print(f"paired_portable_ms={portable_times!r} median={median(portable_times):.3f}")
        print(f"paired_optimized_ms={optimized_times!r} median={median(optimized_times):.3f}")
    finally:
        connection.close()


def print_operators(node: dict[str, object]) -> None:
    children = node.get("children", [])
    if not isinstance(children, list):
        return
    name = node.get("operator_name")
    if name in {"ORDER_BY", "HASH_GROUP_BY", "FILTER", "WINDOW", "DUCKLAKE_SCAN"}:
        child_cardinality = None
        if children and isinstance(children[0], dict):
            child_cardinality = children[0].get("operator_cardinality")
        print(
            f"operator={name} before={child_cardinality} "
            f"after={node.get('operator_cardinality')} "
            f"scanned={node.get('operator_rows_scanned')}"
        )
    for child in children:
        if isinstance(child, dict):
            print_operators(child)


if __name__ == "__main__":
    main()
