"""Read-only DuckLake hot-path benchmark against an existing repository."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from repository.catalogue.client import Catalogue
from repository.catalogue.query import execute_arrow_query, prepare_catalogue_query
from repository.catalogue.service import CatalogueService


_REPRESENTATIVE_QUERY_LIMIT = 1_000
_TEXT_EXTRACTION_LIMIT = 100


def run_hot_path_benchmark(
    catalogue: Catalogue,
    *,
    samples: int = 100,
    query_repetitions: int = 3,
) -> dict[str, Any]:
    """Measure service reads and representative partition/DOM SQL workloads."""

    if samples <= 0:
        raise ValueError("samples must be greater than zero")
    if query_repetitions <= 0:
        raise ValueError("query_repetitions must be greater than zero")
    service = CatalogueService(catalogue)
    table = service._table
    document_rows = catalogue.connection.execute(
        "SELECT document_id FROM "
        f"{table('documents')} LIMIT ?",
        [samples],
    ).fetchall()
    crawl_rows = catalogue.connection.execute(
        "SELECT document_id, normalized_url, page_url, config_hash, captured_at FROM "
        f"{table('crawls')} WHERE document_id IS NOT NULL "
        "LIMIT ?",
        [samples],
    ).fetchall()
    document_ids = [str(row[0]) for row in document_rows]

    started = time.perf_counter()
    resolved = service.get_documents(document_ids)
    document_batch_seconds = time.perf_counter() - started

    cache_timings = [
        _timed(
            lambda normalized_url=normalized_url, config_hash=config_hash: (
                service.find_cached_crawls(
                    normalized_url=str(normalized_url),
                    config_hash=str(config_hash),
                    limit=1,
                )
            )
        )
        for _, normalized_url, _, config_hash, _ in crawl_rows
    ]
    element_timings = [
        _timed(lambda document_id=document_id: service.get_elements(document_id, limit=100))
        for document_id in document_ids
    ]
    link_samples = crawl_rows[: min(len(crawl_rows), 20)]
    link_timings = [
        _timed(
            lambda document_id=document_id, page_url=page_url: (
                service.get_projected_links(
                    str(document_id),
                    page_url=str(page_url),
                )
            )
        )
        for document_id, _, page_url, _, _ in link_samples
    ]
    return {
        "samples": {
            "documents": len(document_ids),
            "crawls": len(crawl_rows),
            "links": len(link_samples),
        },
        "document_batch_lookup": {
            "seconds": document_batch_seconds,
            "requested": len(document_ids),
            "found": len(resolved),
        },
        "cache_lookup": _timing_summary(cache_timings),
        "element_page_100": _timing_summary(element_timings),
        "link_projection": _timing_summary(link_timings),
        "representative_queries": _representative_queries(
            catalogue,
            crawl_rows=crawl_rows,
            repetitions=query_repetitions,
        ),
    }


def print_hot_path_benchmark(
    catalogue: Catalogue,
    *,
    samples: int,
    query_repetitions: int,
) -> None:
    print(
        json.dumps(
            run_hot_path_benchmark(
                catalogue,
                samples=samples,
                query_repetitions=query_repetitions,
            ),
            indent=2,
        )
    )


def _representative_queries(
    catalogue: Catalogue,
    *,
    crawl_rows: list[tuple[Any, ...]],
    repetitions: int,
) -> dict[str, Any]:
    if not crawl_rows:
        return {
            "available": False,
            "reason": "catalogue has no crawls with DOM documents",
            "repetitions": repetitions,
        }

    document_id = str(crawl_rows[0][0])
    captured_at = crawl_rows[0][4]
    if not isinstance(captured_at, datetime):
        raise TypeError("catalogue returned a non-datetime captured_at value")
    window_start = captured_at.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=1)
    window_parameters = {
        "window_start": window_start,
        "window_end": window_end,
        "limit": _REPRESENTATIVE_QUERY_LIMIT,
    }

    queries = {
        "date_bounded_crawls": (
            """
            SELECT crawl_id, document_id, captured_at, normalized_url
            FROM crawls
            WHERE captured_at >= $window_start
              AND captured_at < $window_end
            ORDER BY captured_at DESC
            LIMIT $limit
            """,
            window_parameters,
        ),
        "document_css_selector": (
            """
            SELECT element_index, get_attribute('href') AS href
            FROM elements
            WHERE document_id = $document_id
              AND css_select('a[href]')
            ORDER BY element_index
            LIMIT $limit
            """,
            {"document_id": document_id, "limit": _REPRESENTATIVE_QUERY_LIMIT},
        ),
        "date_bounded_crawl_element_join": (
            """
            WITH bounded_crawls AS MATERIALIZED (
                SELECT crawl_id, document_id, captured_at
                FROM crawls
                WHERE captured_at >= $window_start
                  AND captured_at < $window_end
                  AND document_id IS NOT NULL
                ORDER BY captured_at DESC
                LIMIT $limit
            )
            SELECT c.crawl_id, c.captured_at, e.element_index,
                   get_attribute(e, 'href') AS href
            FROM bounded_crawls AS c
            JOIN elements AS e USING (document_id)
            WHERE css_select(e, 'a[href]')
            LIMIT $limit
            """,
            window_parameters,
        ),
        "bounded_readable_text": (
            """
            WITH selected AS MATERIALIZED (
                SELECT document_id, element_index
                FROM elements
                WHERE document_id = $document_id
                ORDER BY element_index
                LIMIT $text_limit
            )
            SELECT document_id, element_index,
                   readable_text(document_id, element_index) AS text
            FROM selected
            """,
            {"document_id": document_id, "text_limit": _TEXT_EXTRACTION_LIMIT},
        ),
    }
    measured = {
        name: _measure_query(
            catalogue,
            sql=sql,
            parameters=parameters,
            repetitions=repetitions,
        )
        for name, (sql, parameters) in queries.items()
    }
    return {
        "available": True,
        "repetitions": repetitions,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "document_id": document_id,
        "workloads": measured,
    }


def _measure_query(
    catalogue: Catalogue,
    *,
    sql: str,
    parameters: dict[str, object],
    repetitions: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_result = execute_arrow_query(catalogue, sql, parameters).read_all()
    first_run_seconds = time.perf_counter() - started

    warm_timings: list[float] = []
    warm_rows: list[int] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        result = execute_arrow_query(catalogue, sql, parameters).read_all()
        warm_timings.append(time.perf_counter() - started)
        warm_rows.append(result.num_rows)
    if any(rows != first_result.num_rows for rows in warm_rows):
        raise RuntimeError("benchmark query returned an unstable row count")
    return {
        "rows": first_result.num_rows,
        "output_bytes": first_result.nbytes,
        "first_run_seconds": first_run_seconds,
        "warm": _timing_summary(warm_timings),
        "warm_profile": _profile_query(catalogue, sql=sql, parameters=parameters),
    }


def _profile_query(
    catalogue: Catalogue,
    *,
    sql: str,
    parameters: dict[str, object],
) -> dict[str, Any]:
    prepared = prepare_catalogue_query(catalogue, sql, parameters)
    catalogue.connection.execute(f"USE {prepared.namespace}")
    explain_sql = f"EXPLAIN (ANALYZE, FORMAT JSON) {prepared.sql}"
    row = (
        catalogue.connection.execute(explain_sql, prepared.bindings).fetchone()
        if prepared.bindings
        else catalogue.connection.execute(explain_sql).fetchone()
    )
    if row is None:
        raise RuntimeError("EXPLAIN ANALYZE returned no profile")
    profile = json.loads(str(row[1]))
    operators = list(_profile_operators(profile))
    return {
        "latency_seconds": profile.get("latency"),
        "cpu_seconds": profile.get("cpu_time"),
        "total_bytes_read": profile.get("total_bytes_read"),
        "peak_buffer_memory_bytes": profile.get("system_peak_buffer_memory"),
        "cumulative_rows_scanned": profile.get("cumulative_rows_scanned"),
        "cumulative_cardinality": profile.get("cumulative_cardinality"),
        "maximum_operator_cardinality": max(
            (int(operator.get("operator_cardinality", 0)) for operator in operators),
            default=0,
        ),
        "scan_operators": [
            {
                "name": operator.get("operator_name"),
                "rows_returned": operator.get("operator_cardinality"),
                "rows_scanned": operator.get("operator_rows_scanned"),
                "seconds": operator.get("operator_timing"),
                "details": operator.get("extra_info", {}),
            }
            for operator in operators
            if operator.get("operator_type") == "TABLE_SCAN"
        ],
    }


def _profile_operators(node: dict[str, Any]) -> list[dict[str, Any]]:
    operators = [node] if "operator_type" in node else []
    for child in node.get("children", []):
        operators.extend(_profile_operators(child))
    return operators


def _timed(operation: Callable[[], object]) -> float:
    started = time.perf_counter()
    operation()
    return time.perf_counter() - started


def _timing_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50_seconds": None, "p95_seconds": None, "max_seconds": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50_seconds": statistics.median(ordered),
        "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max_seconds": ordered[-1],
    }
