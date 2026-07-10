"""Read-only repository hot-path benchmark against an existing corpus."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from typing import Any

from catalogue.client import Catalogue
from catalogue.service import CatalogueService


def run_hot_path_benchmark(
    catalogue: Catalogue,
    *,
    samples: int = 100,
) -> dict[str, Any]:
    """Measure representative point, cache, projection, and link reads."""

    if samples <= 0:
        raise ValueError("samples must be greater than zero")
    service = CatalogueService(catalogue)
    table = service._table
    document_rows = catalogue.connection.execute(
        "SELECT document_id FROM "
        f"{table('documents')} LIMIT ?",
        [samples],
    ).fetchall()
    crawl_rows = catalogue.connection.execute(
        "SELECT document_id, normalized_url, final_url, input_hash FROM "
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
            lambda normalized_url=normalized_url, input_hash=input_hash: (
                service.find_cached_crawls(
                    normalized_url=str(normalized_url),
                    input_hash=str(input_hash),
                    limit=1,
                )
            )
        )
        for _, normalized_url, _, input_hash in crawl_rows
    ]
    element_timings = [
        _timed(lambda document_id=document_id: service.get_elements(document_id, limit=100))
        for document_id in document_ids
    ]
    link_samples = crawl_rows[: min(len(crawl_rows), 20)]
    link_timings = [
        _timed(
            lambda document_id=document_id, normalized_url=normalized_url, final_url=final_url: (
                service.get_projected_links(
                    str(document_id),
                    page_url=str(final_url or normalized_url),
                )
            )
        )
        for document_id, normalized_url, final_url, _ in link_samples
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
    }


def print_hot_path_benchmark(catalogue: Catalogue, *, samples: int) -> None:
    print(json.dumps(run_hot_path_benchmark(catalogue, samples=samples), indent=2))


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
