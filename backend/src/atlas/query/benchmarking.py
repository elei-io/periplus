"""Repeatable measurements of real public Atlas SQL.

This module only conducts benchmarks. Production query optimization remains in
the Atlas DuckDB extension and the public catalogue.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter
import tomllib
from typing import Any, Iterable

import duckdb

from atlas.platform.catalogue.config import catalogue_config_from_env
from atlas.platform.catalogue.connection import DuckLakeConnectionFactory


BLOCKING_OPERATORS = frozenset(
    {
        "HASH_GROUP_BY",
        "PERFECT_HASH_GROUP_BY",
        "ORDER_BY",
        "TOP_N",
        "WINDOW",
        "HASH_JOIN",
        "RIGHT_DELIM_JOIN",
        "LEFT_DELIM_JOIN",
        "MATERIALIZED_CTE",
        "UNNEST",
        "DISTINCT",
    }
)


@dataclass(frozen=True, slots=True)
class QueryCase:
    identifier: str
    title: str
    use_case: str
    classification: str
    ordered: bool
    scales: tuple[int | None, ...]
    memory_limit: str
    max_warm_ms: float
    sql: str
    directory: Path


@dataclass(frozen=True, slots=True)
class Measurement:
    case: str
    scale: int | None
    ducklake_snapshot: int
    normal_ms: float
    warm_ms: tuple[float, ...]
    median_warm_ms: float
    result_rows: int
    result_digest: str
    columns: tuple[str, ...]
    types: tuple[str, ...]
    cumulative_rows_scanned: tuple[int, ...]
    total_bytes_read: tuple[int, ...]
    peak_buffer_bytes: tuple[int, ...]
    peak_temp_bytes: tuple[int, ...]
    blocking_operators: tuple[dict[str, Any], ...]
    scans: tuple[dict[str, Any], ...]
    within_time_budget: bool


def discover_cases(root: Path) -> dict[str, QueryCase]:
    cases: dict[str, QueryCase] = {}
    for metadata_path in sorted(root.glob("*/case.toml")):
        case = load_case(metadata_path.parent)
        if case.identifier in cases:
            raise ValueError(f"duplicate query benchmark case: {case.identifier}")
        cases[case.identifier] = case
    if not cases:
        raise ValueError(f"no query benchmark cases found below {root}")
    return cases


def load_case(directory: Path) -> QueryCase:
    metadata = tomllib.loads((directory / "case.toml").read_text(encoding="utf-8"))
    sql = (directory / "query.sql").read_text(encoding="utf-8").strip()
    identifier = _required_string(metadata, "id")
    if identifier != directory.name:
        raise ValueError(f"case id {identifier!r} must match directory {directory.name!r}")
    classification = _required_string(metadata, "classification")
    if classification not in {"schema", "optimizer", "both", "unclassified"}:
        raise ValueError(f"invalid classification for {identifier}: {classification}")
    scales_value = metadata.get("scales", [])
    if not isinstance(scales_value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in scales_value
    ):
        raise ValueError(f"scales for {identifier} must be positive integers")
    scales: tuple[int | None, ...] = tuple(scales_value) or (None,)
    if "$scope" in sql and scales == (None,):
        raise ValueError(f"case {identifier} uses $scope but defines no scales")
    if "$scope" not in sql and scales != (None,):
        raise ValueError(f"case {identifier} defines scales but query.sql has no $scope")
    return QueryCase(
        identifier=identifier,
        title=_required_string(metadata, "title"),
        use_case=_required_string(metadata, "use_case"),
        classification=classification,
        ordered=bool(metadata.get("ordered", False)),
        scales=scales,
        memory_limit=_required_string(metadata, "memory_limit"),
        max_warm_ms=float(metadata.get("max_warm_ms", 60_000)),
        sql=sql,
        directory=directory,
    )


def _required_string(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"case metadata {key!r} must be a non-empty string")
    return value.strip()


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _connection(case: QueryCase) -> duckdb.DuckDBPyConnection:
    config = catalogue_config_from_env()
    connection = DuckLakeConnectionFactory(config).connect(
        read_only=True, override_data_path=True
    )
    connection.execute(f'USE "{config.alias.replace(chr(34), chr(34) * 2)}"')
    connection.execute("SET threads = 4")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute(f"SET memory_limit = {_literal(case.memory_limit)}")
    return connection


def _parameters(scale: int | None) -> dict[str, int] | None:
    return {"scope": scale} if scale is not None else None


def _execute(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: dict[str, int] | None,
) -> duckdb.DuckDBPyConnection:
    if parameters is None:
        return connection.execute(sql)
    return connection.execute(sql, parameters)


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
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def result_digest(rows: Iterable[tuple[Any, ...]], *, ordered: bool) -> str:
    rendered = [
        json.dumps(_canonical(row), sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    if not ordered:
        rendered.sort()
    return hashlib.sha256("\n".join(rendered).encode()).hexdigest()


def inspect_profile(
    profile: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    blocking: list[dict[str, Any]] = []
    scans: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], path: str) -> None:
        name = str(node.get("operator_name", ""))
        children = [
            child for child in (node.get("children") or []) if isinstance(child, dict)
        ]
        extra = node.get("extra_info") or {}
        if name in BLOCKING_OPERATORS:
            blocking.append(
                {
                    "path": path,
                    "operator": name,
                    "input_cardinalities": [
                        child.get("operator_cardinality") for child in children
                    ],
                    "output_cardinality": node.get("operator_cardinality"),
                    "rows_scanned": node.get("operator_rows_scanned"),
                }
            )
        if "SCAN" in name:
            scans.append(
                {
                    "path": path,
                    "operator": name,
                    "catalog": extra.get("Catalog"),
                    "schema": extra.get("Schema"),
                    "table": extra.get("Table"),
                    "output_cardinality": node.get("operator_cardinality"),
                    "rows_scanned": node.get("operator_rows_scanned"),
                    "files_read": extra.get("Total Files Read"),
                    "filters": extra.get("Filters"),
                }
            )
        for index, child in enumerate(children):
            visit(child, f"{path}/{name or 'ROOT'}[{index}]")

    visit(profile, "ROOT")
    return tuple(blocking), tuple(scans)


def measure_case(case: QueryCase, scale: int | None, *, warm_runs: int) -> Measurement:
    connection = _connection(case)
    parameters = _parameters(scale)
    try:
        connection.execute("BEGIN TRANSACTION")
        config = catalogue_config_from_env()
        snapshot = int(
            connection.execute(
                "SELECT max(snapshot_id) FROM ducklake_snapshots(?)", [config.alias]
            ).fetchone()[0]
        )
        started = perf_counter()
        cursor = _execute(connection, case.sql, parameters)
        rows = cursor.fetchall()
        normal_ms = (perf_counter() - started) * 1000
        description = cursor.description or []
        columns = tuple(str(column[0]) for column in description)
        types = tuple(str(column[1]) for column in description)
        warm_ms: list[float] = []
        rows_scanned: list[int] = []
        bytes_read: list[int] = []
        peak_buffer: list[int] = []
        peak_temp: list[int] = []
        blocking: tuple[dict[str, Any], ...] = ()
        scans: tuple[dict[str, Any], ...] = ()
        for _ in range(warm_runs):
            cursor = _execute(
                connection,
                "EXPLAIN (ANALYZE, FORMAT JSON) " + case.sql,
                parameters,
            )
            profile = json.loads(cursor.fetchone()[1])
            warm_ms.append(float(profile.get("latency", 0)) * 1000)
            rows_scanned.append(int(profile.get("cumulative_rows_scanned", 0)))
            bytes_read.append(int(profile.get("total_bytes_read", 0)))
            peak_buffer.append(int(profile.get("system_peak_buffer_memory", 0)))
            peak_temp.append(int(profile.get("system_peak_temp_dir_size", 0)))
            blocking, scans = inspect_profile(profile)
        warm_median = median(warm_ms)
        return Measurement(
            case=case.identifier,
            scale=scale,
            ducklake_snapshot=snapshot,
            normal_ms=normal_ms,
            warm_ms=tuple(warm_ms),
            median_warm_ms=warm_median,
            result_rows=len(rows),
            result_digest=result_digest(rows, ordered=case.ordered),
            columns=columns,
            types=types,
            cumulative_rows_scanned=tuple(rows_scanned),
            total_bytes_read=tuple(bytes_read),
            peak_buffer_bytes=tuple(peak_buffer),
            peak_temp_bytes=tuple(peak_temp),
            blocking_operators=blocking,
            scans=scans,
            within_time_budget=warm_median <= case.max_warm_ms,
        )
    finally:
        # A DuckDB internal error invalidates the connection. Preserve the
        # original benchmark failure instead of replacing it with ROLLBACK's
        # secondary error during cleanup.
        with suppress(duckdb.Error):
            connection.execute("ROLLBACK")
        connection.close()


def environment_metadata() -> dict[str, Any]:
    config = catalogue_config_from_env()
    connection = DuckLakeConnectionFactory(config).connect(
        read_only=True, override_data_path=True
    )
    try:
        connection.execute(f'USE "{config.alias.replace(chr(34), chr(34) * 2)}"')
        snapshot = connection.execute(
            "SELECT max(snapshot_id) FROM ducklake_snapshots(?)", [config.alias]
        ).fetchone()[0]
        extension = connection.execute(
            "SELECT extension_version FROM duckdb_extensions() "
            "WHERE extension_name = 'atlas' AND loaded LIMIT 1"
        ).fetchone()
        return {
            "recorded_at": datetime.now().astimezone().isoformat(),
            "duckdb_version": connection.execute("SELECT version()").fetchone()[0],
            "atlas_extension_version": extension[0] if extension else None,
            "catalogue_version": connection.execute(
                "SELECT web._catalogue_version()"
            ).fetchone()[0],
            "ducklake_snapshot": snapshot,
            "catalogue_alias": config.alias,
        }
    finally:
        connection.close()


def compare_reports(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    baseline_by_key = {
        (item["case"], item.get("scale")): item
        for item in baseline.get("measurements", [])
    }
    comparisons: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in candidate.get("measurements", []):
        key = (item["case"], item.get("scale"))
        reference = baseline_by_key.get(key)
        if reference is None:
            failures.append(f"missing baseline for {key[0]} scale={key[1]}")
            continue
        same_snapshot = item.get("ducklake_snapshot") == reference.get(
            "ducklake_snapshot"
        )
        if not same_snapshot:
            failures.append(
                f"snapshot mismatch for {key[0]} scale={key[1]}: "
                f"baseline={reference.get('ducklake_snapshot')} "
                f"candidate={item.get('ducklake_snapshot')}"
            )
        exact = (
            same_snapshot
            and tuple(item["columns"]) == tuple(reference["columns"])
            and tuple(item["types"]) == tuple(reference["types"])
            and item["result_rows"] == reference["result_rows"]
            and item["result_digest"] == reference["result_digest"]
        )
        if not exact:
            if same_snapshot:
                failures.append(f"result mismatch for {key[0]} scale={key[1]}")
        baseline_ms = float(reference["median_warm_ms"])
        candidate_ms = float(item["median_warm_ms"])
        comparisons.append(
            {
                "case": key[0],
                "scale": key[1],
                "exact_result": exact,
                "warm_time_ratio": candidate_ms / baseline_ms if baseline_ms else None,
                "peak_buffer_ratio": _ratio_of_max(
                    item["peak_buffer_bytes"], reference["peak_buffer_bytes"]
                ),
                "rows_scanned_ratio": _ratio_of_max(
                    item["cumulative_rows_scanned"],
                    reference["cumulative_rows_scanned"],
                ),
            }
        )
    return comparisons, failures


def _ratio_of_max(candidate: list[int], baseline: list[int]) -> float | None:
    baseline_max = max(baseline, default=0)
    return max(candidate, default=0) / baseline_max if baseline_max else None


def report_payload(
    cases: Iterable[QueryCase], *, warm_runs: int
) -> dict[str, Any]:
    measurements: list[Measurement] = []
    case_metadata: list[dict[str, Any]] = []
    for case in cases:
        case_metadata.append(
            {
                "id": case.identifier,
                "title": case.title,
                "use_case": case.use_case,
                "classification": case.classification,
                "ordered": case.ordered,
                "memory_limit": case.memory_limit,
                "max_warm_ms": case.max_warm_ms,
                "sql": case.sql,
            }
        )
        for scale in case.scales:
            measurement = measure_case(case, scale, warm_runs=warm_runs)
            measurements.append(measurement)
            print(
                f"case={case.identifier} scale={scale} "
                f"normal_ms={measurement.normal_ms:.1f} "
                f"warm_median_ms={measurement.median_warm_ms:.1f} "
                f"peak_mib={max(measurement.peak_buffer_bytes) / 1048576:.1f} "
                f"rows_scanned={max(measurement.cumulative_rows_scanned)} "
                f"result_rows={measurement.result_rows}",
                flush=True,
            )
    return {
        "format_version": 1,
        "environment": environment_metadata(),
        "warm_runs": warm_runs,
        "cases": case_metadata,
        "measurements": [asdict(item) for item in measurements],
    }
