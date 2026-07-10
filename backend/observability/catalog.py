from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MetricKind = Literal["counter", "gauge", "histogram"]

QUEUE_WAIT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
PAGE_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
TASK_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)
SIZE_BUCKETS = (1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864)


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: MetricKind
    help: str
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()


DEFINITIONS = {
    definition.name: definition
    for definition in (
        MetricDefinition("atlas_task_run_queue_duration_seconds", "histogram", "Time a task run waited to be claimed.", ("primitive",), QUEUE_WAIT_BUCKETS),
        MetricDefinition("atlas_task_run_execution_duration_seconds", "histogram", "Task run execution duration.", ("primitive", "status"), TASK_BUCKETS),
        MetricDefinition("atlas_task_runs_total", "counter", "Terminal task runs.", ("primitive", "status", "trigger_kind")),
        MetricDefinition("atlas_task_run_attempts_total", "counter", "Completed task run attempts.", ("primitive", "outcome")),
        MetricDefinition("atlas_task_run_recoveries_total", "counter", "Recovered task run leases.", ("reason",)),
        MetricDefinition("atlas_task_run_cancellations_total", "counter", "Cancelled task runs.", ("phase",)),
        MetricDefinition("atlas_crawl_permit_waiters", "gauge", "Current capacity waiters owned by this worker.", ("scope", "policy")),
        MetricDefinition("atlas_crawl_permit_wait_duration_seconds", "histogram", "Time spent acquiring crawl permits.", ("blocked_scope", "policy", "outcome"), QUEUE_WAIT_BUCKETS),
        MetricDefinition("atlas_crawl_permit_timeouts_total", "counter", "Crawl permit acquisition timeouts.", ("blocked_scope", "policy")),
        MetricDefinition("atlas_crawl_permit_lease_losses_total", "counter", "Crawl permit lease losses.", ("scope", "policy")),
        MetricDefinition("atlas_page_acquisitions_total", "counter", "Logical page acquisition outcomes.", ("outcome", "mode", "source", "domain_group")),
        MetricDefinition("atlas_page_acquisition_duration_seconds", "histogram", "Logical page acquisition duration after capacity was acquired.", ("domain_group", "mode", "outcome", "source"), PAGE_BUCKETS),
        MetricDefinition("atlas_crawl_navigation_duration_seconds", "histogram", "Network navigation duration.", ("domain_group", "mode", "outcome"), PAGE_BUCKETS),
        MetricDefinition("atlas_crawls_persisted_total", "counter", "New durable crawl rows committed.", ("outcome", "mode", "domain_group")),
        MetricDefinition("atlas_crawl_http_responses_total", "counter", "Logical page acquisitions by HTTP status class.", ("status_class", "domain_group")),
        MetricDefinition("atlas_crawl_failures_total", "counter", "Crawl failures by normalized reason.", ("reason", "domain_group", "mode")),
        MetricDefinition("atlas_crawl_retries_total", "counter", "Crawl retries by normalized reason.", ("reason", "domain_group")),
        MetricDefinition("atlas_crawl_redirects_total", "counter", "Crawl redirects.", ("domain_group",)),
        MetricDefinition("atlas_crawl_response_size_bytes", "histogram", "Acquired HTML response size.", ("domain_group",), SIZE_BUCKETS),
        MetricDefinition("atlas_crawl_warnings_total", "counter", "Crawl warnings by kind.", ("kind", "domain_group")),
        MetricDefinition("atlas_metric_observations_dropped_total", "counter", "Metric observations dropped by the worker transport.", ("reason",)),
    )
}


def definition_for(name: str) -> MetricDefinition:
    try:
        return DEFINITIONS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Atlas metric {name!r}.") from exc


def validate_labels(definition: MetricDefinition, labels: dict[str, str]) -> dict[str, str]:
    if set(labels) != set(definition.labels):
        raise ValueError(
            f"{definition.name} expects labels {definition.labels!r}, got {tuple(labels)!r}."
        )
    return {name: str(labels[name]) for name in definition.labels}
