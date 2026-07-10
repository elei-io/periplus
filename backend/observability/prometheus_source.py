from __future__ import annotations

import math
import os
from urllib.parse import urljoin

import httpx

from .schemas import PrometheusOperationsMetrics


def _duration(window_seconds: int) -> str:
    if window_seconds % 3600 == 0:
        return f"{window_seconds // 3600}h"
    if window_seconds % 60 == 0:
        return f"{window_seconds // 60}m"
    return f"{window_seconds}s"


def _scalar(client: httpx.Client, base_url: str, query: str) -> float | None:
    response = client.get(urljoin(base_url.rstrip("/") + "/", "api/v1/query"), params={"query": query})
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus query failed.")
    results = payload.get("data", {}).get("result", [])
    if not results:
        return 0.0
    value = float(results[0]["value"][1])
    return value if math.isfinite(value) else None


def collect_prometheus_metrics(window_seconds: int) -> PrometheusOperationsMetrics:
    base_url = os.getenv("ATLAS_PROMETHEUS_URL", "").strip()
    if not base_url:
        return PrometheusOperationsMetrics(configured=False, available=False)

    window = _duration(window_seconds)
    queries = {
        "capacity_waiters": "sum(atlas_crawl_permit_waiters)",
        "capacity_wait_p95_seconds": (
            "histogram_quantile(0.95, "
            f"sum(rate(atlas_crawl_permit_wait_duration_seconds_bucket[{window}])) by (le))"
        ),
        "page_acquisitions_per_second": (
            f"sum(increase(atlas_page_acquisitions_total[{window}])) / {window_seconds}"
        ),
        "page_success_ratio": (
            f"sum(increase(atlas_page_acquisitions_total{{outcome=\"succeeded\"}}[{window}])) "
            f"/ clamp_min(sum(increase(atlas_page_acquisitions_total{{outcome=~\"succeeded|failed\"}}[{window}])), 1)"
        ),
        "dropped_observations": (
            f"sum(increase(atlas_metric_observations_dropped_total[{window}]))"
        ),
    }
    try:
        with httpx.Client(timeout=1.0) as client:
            values = {
                name: _scalar(client, base_url, query)
                for name, query in queries.items()
            }
    except Exception as exc:
        return PrometheusOperationsMetrics(
            configured=True,
            available=False,
            error=str(exc),
        )
    return PrometheusOperationsMetrics(configured=True, available=True, **values)
