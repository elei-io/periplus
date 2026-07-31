"""Measure exact bounded DOM selector execution against the live Atlas DuckLake.

The benchmark is read-only with respect to DuckLake. It captures one immutable
content scope and compares the public lateral plan, the native keyed operator,
and research-only ephemeral hydration strategies. Any staging remains in
connection-local temporary tables.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, Iterable

import duckdb

from atlas.platform.catalogue.config import catalogue_config_from_env
from atlas.platform.catalogue.connection import DuckLakeConnectionFactory


DEFAULT_SCOPES = (100, 500, 1_000)
DEFAULT_PROBE_BATCH = 8
SELECTOR = "main a[href]"
STRATEGIES = (
    "authored_lateral",
    "native_keyed_selector",
    "single_in_stage",
    "bounded_probe_stage",
    "bounded_selector_stage",
)


@dataclass(frozen=True, slots=True)
class ContentKey:
    content_id: str
    bucket: int
    rank: int


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    latency_ms: float
    total_bytes_read: int
    cumulative_rows_scanned: int
    peak_buffer_bytes: int
    peak_temp_bytes: int
    element_scan_output_rows: int
    element_scan_files: int
    blocking_operators: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class Measurement:
    scope_documents: int
    strategy: str
    normal_ms: float
    warm_ms: tuple[float, ...]
    median_warm_ms: float
    total_bytes_read: tuple[int, ...]
    cumulative_rows_scanned: tuple[int, ...]
    peak_buffer_bytes: tuple[int, ...]
    peak_temp_bytes: tuple[int, ...]
    element_scan_output_rows: tuple[int, ...]
    element_scan_files: tuple[int, ...]
    staged_rows: int | None
    result_rows: int
    result_digest: str
    blocking_operators: tuple[dict[str, Any], ...]


SCOPED_SELECT_MACRO = """
CREATE OR REPLACE TEMP MACRO bench_scoped_select_all(
    selected_content_id, css_selector
) AS TABLE
SELECT
    content_id,
    element_index,
    parent_index AS parent_element_index,
    subtree_end_index,
    depth,
    child_index AS sibling_index,
    tag AS tag_name,
    namespace,
    attributes,
    text_direct AS direct_text,
    text_tail AS tail_text
FROM atlas_dom_select_all(
    (
        SELECT
            element.content_id,
            element.element_index,
            element.parent_element_index AS parent_index,
            element.subtree_end_index,
            element.depth,
            element.sibling_index AS child_index,
            element.tag_name AS tag,
            element.namespace,
            element.attributes,
            element.direct_text AS text_direct,
            element.tail_text AS text_tail,
            false AS _atlas_document_end
        FROM bench_scoped_elements AS element
        JOIN (SELECT selected_content_id AS content_id)
             AS selected USING (content_id)
        UNION ALL
        SELECT
            selected_content_id,
            NULL::INTEGER, NULL::INTEGER, NULL::INTEGER, NULL::INTEGER,
            NULL::INTEGER, NULL::VARCHAR, NULL::VARCHAR,
            NULL::MAP(VARCHAR, VARCHAR), NULL::VARCHAR, NULL::VARCHAR,
            true
        ORDER BY content_id, _atlas_document_end, element_index
    ),
    css_selector
)
"""

NATIVE_KEYED_SELECT_MACRO = """
CREATE OR REPLACE TEMP MACRO bench_native_keyed_select_all(
    selected_content_id, css_selector
) AS TABLE
SELECT
    content_id,
    element_index,
    parent_index AS parent_element_index,
    subtree_end_index,
    depth,
    child_index AS sibling_index,
    tag AS tag_name,
    namespace,
    attributes,
    text_direct AS direct_text,
    text_tail AS tail_text
FROM atlas_dom_select_all_keyed(
    (SELECT selected_content_id AS content_id),
    css_selector
)
"""


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _connection() -> duckdb.DuckDBPyConnection:
    connection = DuckLakeConnectionFactory(
        catalogue_config_from_env()
    ).connect(read_only=True, override_data_path=True)
    alias = catalogue_config_from_env().alias
    connection.execute(f"USE {_identifier(alias)}")
    connection.execute("SET threads = 4")
    connection.execute("SET preserve_insertion_order = false")
    memory_limit = os.getenv("ATLAS_BENCH_MEMORY_LIMIT")
    if memory_limit:
        connection.execute(f"SET memory_limit = {_literal(memory_limit)}")
    return connection


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _capture_scope(maximum: int) -> tuple[tuple[ContentKey, ...], float]:
    connection = _connection()
    started = perf_counter()
    try:
        rows = connection.execute(
            f"""
            WITH candidates AS (
                SELECT DISTINCT visit.content_id
                FROM web.page AS page
                JOIN web.page_visit AS visit
                  ON visit.page_visit_id = page.latest_page_visit_id
                WHERE visit.content_id IS NOT NULL
            ),
            selected AS (
                SELECT content_id,
                       row_number() OVER (
                           ORDER BY hash(content_id), content_id
                       ) AS content_rank
                FROM candidates
                ORDER BY content_rank
                LIMIT {maximum}
            )
            SELECT content_id,
                   ((murmur3_32(content_id) & 2147483647) % 8)::INTEGER
                       AS content_bucket,
                   content_rank
            FROM selected
            ORDER BY content_rank
            """
        ).fetchall()
    finally:
        connection.close()
    keys = tuple(ContentKey(str(row[0]), int(row[1]), int(row[2])) for row in rows)
    if len(keys) < maximum:
        raise RuntimeError(
            f"requested {maximum} contents, but the lake supplied only {len(keys)}"
        )
    return keys, (perf_counter() - started) * 1000


def _install_scope(
    connection: duckdb.DuckDBPyConnection,
    keys: tuple[ContentKey, ...],
) -> None:
    values = ",".join(
        f"({_literal(key.content_id)}, {key.bucket}, {key.rank})"
        for key in keys
    )
    connection.execute(
        "CREATE TEMP TABLE bench_scope AS "
        "SELECT content_id::VARCHAR AS content_id, "
        "bucket::INTEGER AS bucket, rank::INTEGER AS rank "
        f"FROM (VALUES {values}) AS scope(content_id, bucket, rank)"
    )


def _result_sql(selector: str) -> str:
    return f"""
        SELECT scope.content_id,
               match.element_index,
               map_extract_value(match.attributes, 'href') AS href
        FROM bench_scope AS scope
        JOIN LATERAL {selector}(
            scope.content_id, {_literal(SELECTOR)}
        ) AS match ON true
        ORDER BY scope.content_id, match.element_index
        LIMIT 5000
    """


def _empty_staging_sql() -> str:
    return """
        CREATE TEMP TABLE bench_scoped_elements AS
        SELECT * FROM dom.element WHERE false
    """


def _single_in_hydration_sql(keys: tuple[ContentKey, ...]) -> str:
    values = ",".join(_literal(key.content_id) for key in keys)
    return """
        CREATE TEMP TABLE bench_scoped_elements AS
        SELECT * FROM dom.element
    """ + f" WHERE content_id IN ({values})"


def _probe_insert_sql(keys: Iterable[ContentKey]) -> str:
    probes = " UNION ALL ".join(
        "SELECT * FROM dom.element WHERE content_id = "
        + _literal(key.content_id)
        for key in keys
    )
    return "INSERT INTO bench_scoped_elements " + probes


def _empty_match_stage_sql() -> str:
    return """
        CREATE TEMP TABLE bench_scoped_matches AS
        SELECT * FROM dom.element WHERE false
    """


def _selector_probe_insert_sql(keys: tuple[ContentKey, ...]) -> str:
    probes = " UNION ALL ".join(
        "SELECT * FROM dom.element WHERE content_id = "
        + _literal(key.content_id)
        for key in keys
    )
    sentinels = ",".join(
        "(" + _literal(key.content_id) + ")" for key in keys
    )
    return f"""
        INSERT INTO bench_scoped_matches
        SELECT
            content_id,
            element_index,
            parent_index AS parent_element_index,
            subtree_end_index,
            depth,
            child_index AS sibling_index,
            tag AS tag_name,
            namespace,
            attributes,
            text_direct AS direct_text,
            text_tail AS tail_text
        FROM atlas_dom_select_all(
            (
                SELECT
                    element.content_id,
                    element.element_index,
                    element.parent_element_index AS parent_index,
                    element.subtree_end_index,
                    element.depth,
                    element.sibling_index AS child_index,
                    element.tag_name AS tag,
                    element.namespace,
                    element.attributes,
                    element.direct_text AS text_direct,
                    element.tail_text AS text_tail,
                    false AS _atlas_document_end
                FROM ({probes}) AS element
                UNION ALL
                SELECT
                    content_id,
                    NULL::INTEGER, NULL::INTEGER, NULL::INTEGER, NULL::INTEGER,
                    NULL::INTEGER, NULL::VARCHAR, NULL::VARCHAR,
                    NULL::MAP(VARCHAR, VARCHAR), NULL::VARCHAR, NULL::VARCHAR,
                    true
                FROM (VALUES {sentinels}) AS selected(content_id)
                ORDER BY content_id, _atlas_document_end, element_index
            ),
            {_literal(SELECTOR)}
        )
    """


def _staged_match_result_sql() -> str:
    return """
        SELECT content_id,
               element_index,
               map_extract_value(attributes, 'href') AS href
        FROM bench_scoped_matches
        ORDER BY content_id, element_index
        LIMIT 5000
    """


def _probe_batches(
    keys: tuple[ContentKey, ...], batch_size: int
) -> tuple[tuple[ContentKey, ...], ...]:
    ordered = sorted(keys, key=lambda key: (key.bucket, key.content_id))
    batches: list[tuple[ContentKey, ...]] = []
    for bucket in range(8):
        bucket_keys = [key for key in ordered if key.bucket == bucket]
        batches.extend(
            tuple(bucket_keys[offset : offset + batch_size])
            for offset in range(0, len(bucket_keys), batch_size)
        )
    return tuple(batch for batch in batches if batch)


def _normal_run(
    keys: tuple[ContentKey, ...], strategy: str, probe_batch: int
) -> tuple[float, list[tuple], int | None]:
    connection = _connection()
    _install_scope(connection, keys)
    connection.execute("BEGIN TRANSACTION")
    started = perf_counter()
    try:
        staged: int | None = None
        if strategy == "authored_lateral":
            sql = _result_sql("dom.query_selector_all")
        elif strategy == "native_keyed_selector":
            connection.execute(NATIVE_KEYED_SELECT_MACRO)
            sql = _result_sql("bench_native_keyed_select_all")
        elif strategy == "bounded_selector_stage":
            connection.execute(_empty_match_stage_sql())
            for batch in _probe_batches(keys, probe_batch):
                connection.execute(_selector_probe_insert_sql(batch))
            staged = int(
                connection.execute(
                    "SELECT count(*) FROM bench_scoped_matches"
                ).fetchone()[0]
            )
            sql = _staged_match_result_sql()
        else:
            if strategy == "single_in_stage":
                connection.execute(_single_in_hydration_sql(keys))
            elif strategy == "bounded_probe_stage":
                connection.execute(_empty_staging_sql())
                for batch in _probe_batches(keys, probe_batch):
                    connection.execute(_probe_insert_sql(batch))
            else:
                raise ValueError(f"unknown strategy: {strategy}")
            staged = int(
                connection.execute(
                    "SELECT count(*) FROM bench_scoped_elements"
                ).fetchone()[0]
            )
            connection.execute(SCOPED_SELECT_MACRO)
            sql = _result_sql("bench_scoped_select_all")
        rows = connection.execute(sql).fetchall()
        return (perf_counter() - started) * 1000, rows, staged
    finally:
        connection.execute("ROLLBACK")
        connection.close()


def _profile_sql(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> tuple[dict[str, Any], ProfileSummary]:
    payload = connection.execute(
        "EXPLAIN (ANALYZE, FORMAT JSON) " + sql
    ).fetchone()[1]
    profile = json.loads(payload)
    return profile, _profile_summary((profile,))


def _profile_summary(
    profiles: Iterable[dict[str, Any]],
) -> ProfileSummary:
    materialized = tuple(profiles)
    element_rows = 0
    element_files = 0
    blocking: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        nonlocal element_rows, element_files
        name = str(node.get("operator_name", ""))
        extra = node.get("extra_info") or {}
        if name == "DUCKLAKE_SCAN" and extra.get("Table") == "html_elements":
            element_rows += int(node.get("operator_cardinality") or 0)
            element_files += int(extra.get("Total Files Read") or 0)
        children = node.get("children") or []
        if name in {
            "HASH_GROUP_BY", "PERFECT_HASH_GROUP_BY", "ORDER_BY", "TOP_N",
            "WINDOW", "HASH_JOIN", "RIGHT_DELIM_JOIN", "LEFT_DELIM_JOIN",
            "MATERIALIZED_CTE", "UNNEST",
        }:
            blocking.append(
                {
                    "operator": name,
                    "before": (
                        children[0].get("operator_cardinality")
                        if children
                        else None
                    ),
                    "after": node.get("operator_cardinality"),
                    "rows_scanned": node.get("operator_rows_scanned"),
                }
            )
        for child in children:
            visit(child)

    for profile in materialized:
        visit(profile)
    return ProfileSummary(
        latency_ms=sum(float(item.get("latency", 0)) for item in materialized)
        * 1000,
        total_bytes_read=sum(
            int(item.get("total_bytes_read", 0)) for item in materialized
        ),
        cumulative_rows_scanned=sum(
            int(item.get("cumulative_rows_scanned", 0)) for item in materialized
        ),
        peak_buffer_bytes=max(
            (int(item.get("system_peak_buffer_memory", 0)) for item in materialized),
            default=0,
        ),
        peak_temp_bytes=max(
            (int(item.get("system_peak_temp_dir_size", 0)) for item in materialized),
            default=0,
        ),
        element_scan_output_rows=element_rows,
        element_scan_files=element_files,
        blocking_operators=tuple(blocking),
    )


def _profile_run(
    keys: tuple[ContentKey, ...], strategy: str, probe_batch: int
) -> ProfileSummary:
    connection = _connection()
    _install_scope(connection, keys)
    connection.execute("BEGIN TRANSACTION")
    profiles: list[dict[str, Any]] = []
    try:
        if strategy == "authored_lateral":
            profile, _ = _profile_sql(
                connection, _result_sql("dom.query_selector_all")
            )
            profiles.append(profile)
        elif strategy == "native_keyed_selector":
            connection.execute(NATIVE_KEYED_SELECT_MACRO)
            profile, _ = _profile_sql(
                connection, _result_sql("bench_native_keyed_select_all")
            )
            profiles.append(profile)
        elif strategy == "bounded_selector_stage":
            connection.execute(_empty_match_stage_sql())
            for batch in _probe_batches(keys, probe_batch):
                profile, _ = _profile_sql(
                    connection, _selector_probe_insert_sql(batch)
                )
                profiles.append(profile)
            profile, _ = _profile_sql(connection, _staged_match_result_sql())
            profiles.append(profile)
        else:
            if strategy == "single_in_stage":
                profile, _ = _profile_sql(
                    connection, _single_in_hydration_sql(keys)
                )
                profiles.append(profile)
            elif strategy == "bounded_probe_stage":
                connection.execute(_empty_staging_sql())
                for batch in _probe_batches(keys, probe_batch):
                    profile, _ = _profile_sql(
                        connection, _probe_insert_sql(batch)
                    )
                    profiles.append(profile)
            else:
                raise ValueError(f"unknown strategy: {strategy}")
            connection.execute(SCOPED_SELECT_MACRO)
            profile, _ = _profile_sql(
                connection, _result_sql("bench_scoped_select_all")
            )
            profiles.append(profile)
        return _profile_summary(profiles)
    finally:
        connection.execute("ROLLBACK")
        connection.close()


def _canonical(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _result_digest(rows: list[tuple]) -> str:
    rendered = sorted(
        json.dumps(_canonical(row), sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return hashlib.sha256("\n".join(rendered).encode()).hexdigest()


def _measure(
    keys: tuple[ContentKey, ...],
    strategy: str,
    *,
    probe_batch: int,
    warm_runs: int,
) -> tuple[Measurement, list[tuple]]:
    normal_ms, rows, staged = _normal_run(keys, strategy, probe_batch)
    profiles = tuple(
        _profile_run(keys, strategy, probe_batch) for _ in range(warm_runs)
    )
    return (
        Measurement(
            scope_documents=len(keys),
            strategy=strategy,
            normal_ms=normal_ms,
            warm_ms=tuple(item.latency_ms for item in profiles),
            median_warm_ms=median(item.latency_ms for item in profiles),
            total_bytes_read=tuple(item.total_bytes_read for item in profiles),
            cumulative_rows_scanned=tuple(
                item.cumulative_rows_scanned for item in profiles
            ),
            peak_buffer_bytes=tuple(item.peak_buffer_bytes for item in profiles),
            peak_temp_bytes=tuple(item.peak_temp_bytes for item in profiles),
            element_scan_output_rows=tuple(
                item.element_scan_output_rows for item in profiles
            ),
            element_scan_files=tuple(item.element_scan_files for item in profiles),
            staged_rows=staged,
            result_rows=len(rows),
            result_digest=_result_digest(rows),
            blocking_operators=profiles[-1].blocking_operators,
        ),
        rows,
    )


def run_benchmark(
    scopes: tuple[int, ...], *, probe_batch: int, warm_runs: int,
    strategies: tuple[str, ...] = STRATEGIES,
) -> dict[str, Any]:
    keys, scope_ms = _capture_scope(max(scopes))
    measurements: list[Measurement] = []
    correctness: dict[str, dict[str, bool]] = {}
    for scope in scopes:
        selected = keys[:scope]
        rows_by_strategy: dict[str, list[tuple]] = {}
        for strategy in strategies:
            measured, rows = _measure(
                selected,
                strategy,
                probe_batch=probe_batch,
                warm_runs=warm_runs,
            )
            measurements.append(measured)
            rows_by_strategy[strategy] = rows
            print(
                f"scope={scope} strategy={strategy} "
                f"normal_ms={measured.normal_ms:.1f} "
                f"warm_median_ms={measured.median_warm_ms:.1f} "
                f"peak_mib={max(measured.peak_buffer_bytes) / 1048576:.1f} "
                f"scan_output={measured.element_scan_output_rows[-1]} "
                f"rows={measured.result_rows}",
                flush=True,
            )
        baseline_strategy = (
            "authored_lateral"
            if "authored_lateral" in rows_by_strategy
            else strategies[0]
        )
        baseline = _result_digest(rows_by_strategy[baseline_strategy])
        correctness[str(scope)] = {
            strategy: _result_digest(rows) == baseline
            for strategy, rows in rows_by_strategy.items()
        }
    return {
        "corpus": {
            "captured_contents": len(keys),
            "scope_capture_ms": scope_ms,
        },
        "selector": SELECTOR,
        "scopes": list(scopes),
        "probe_batch": probe_batch,
        "warm_runs": warm_runs,
        "correctness": correctness,
        "measurements": [asdict(item) for item in measurements],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scopes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SCOPES),
    )
    parser.add_argument("--probe-batch", type=int, default=DEFAULT_PROBE_BATCH)
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=STRATEGIES,
        default=list(STRATEGIES),
    )
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    scopes = tuple(sorted(set(arguments.scopes)))
    if not scopes or scopes[0] < 1:
        parser.error("--scopes must contain positive integers")
    if arguments.probe_batch < 1:
        parser.error("--probe-batch must be positive")
    if arguments.warm_runs < 1:
        parser.error("--warm-runs must be positive")
    report = run_benchmark(
        scopes,
        probe_batch=arguments.probe_batch,
        warm_runs=arguments.warm_runs,
        strategies=tuple(arguments.strategies),
    )
    if arguments.report is not None:
        arguments.report.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"report={arguments.report.resolve()}")
    failures = {
        scope: strategies
        for scope, strategies in report["correctness"].items()
        if not all(strategies.values())
    }
    print(f"correctness_failures={failures!r}")


if __name__ == "__main__":
    main()
