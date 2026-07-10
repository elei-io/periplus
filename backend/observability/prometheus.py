from __future__ import annotations

import threading
from functools import lru_cache

from prometheus_client import CollectorRegistry, Counter, GCCollector, Gauge, Histogram, PlatformCollector, ProcessCollector, generate_latest, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from db import SessionLocal

from .catalog import DEFINITIONS
from .cluster import collect_cluster_metrics
from .recorder import MetricObservation


class ClusterCollector:
    def __init__(self) -> None:
        self._errors = 0
        self._lock = threading.Lock()

    def collect(self):
        try:
            with SessionLocal() as session:
                snapshot = collect_cluster_metrics(session)
        except Exception:
            with self._lock:
                self._errors += 1
                errors = self._errors
            failed = CounterMetricFamily(
                "atlas_metrics_collection_errors_total",
                "Cluster metric collection errors.",
                labels=["collector"],
            )
            failed.add_metric(["cluster"], errors)
            yield failed
            return
        scalar_values = (
            ("atlas_workers_live", "Live Atlas workers.", snapshot.workers_live),
            ("atlas_workers_stale", "Recently stale Atlas workers.", snapshot.workers_stale),
            ("atlas_worker_capacity", "Total live worker task capacity.", snapshot.worker_capacity),
            ("atlas_worker_active_runs", "Active runs held by live workers.", snapshot.worker_active_runs),
            ("atlas_oldest_live_worker_heartbeat_age_seconds", "Age of the oldest live worker heartbeat.", snapshot.oldest_live_worker_heartbeat_age_seconds),
            ("atlas_task_run_oldest_queued_age_seconds", "Age of the oldest queued task run.", snapshot.oldest_queued_age_seconds),
        )
        for name, help_text, value in scalar_values:
            metric = GaugeMetricFamily(name, help_text)
            metric.add_metric([], value)
            yield metric
        queued = GaugeMetricFamily("atlas_task_runs_queued", "Queued task runs.", labels=["primitive"])
        running = GaugeMetricFamily("atlas_task_runs_running", "Running task runs.", labels=["primitive"])
        for task in snapshot.tasks:
            queued.add_metric([task.primitive], task.queued)
            running.add_metric([task.primitive], task.running)
        yield queued
        yield running
        in_use = GaugeMetricFamily("atlas_crawl_permits_in_use", "Unexpired crawl permits in use.", labels=["scope", "policy"])
        capacity = GaugeMetricFamily("atlas_crawl_permit_capacity", "Configured crawl permit capacity.", labels=["scope", "policy"])
        for permit in snapshot.permits:
            labels = [permit.scope, permit.policy]
            in_use.add_metric(labels, permit.in_use)
            capacity.add_metric(labels, permit.capacity)
        yield in_use
        yield capacity
        failed = CounterMetricFamily(
            "atlas_metrics_collection_errors_total",
            "Cluster metric collection errors.",
            labels=["collector"],
        )
        with self._lock:
            errors = self._errors
        failed.add_metric(["cluster"], errors)
        yield failed


def _runtime_registry(*, cluster: bool) -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=False)
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    if cluster:
        registry.register(ClusterCollector())
    return registry


@lru_cache
def api_registry() -> CollectorRegistry:
    return _runtime_registry(cluster=True)


def api_metrics_payload() -> bytes:
    return generate_latest(api_registry())


class WorkerMetricAggregator:
    def __init__(self) -> None:
        self.registry = _runtime_registry(cluster=False)
        self._collectors = {}
        self._snapshots: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = {}
        self._lock = threading.Lock()
        for definition in DEFINITIONS.values():
            kwargs = {"labelnames": definition.labels, "registry": self.registry}
            if definition.kind == "counter":
                collector = Counter(definition.name, definition.help, **kwargs)
            elif definition.kind == "histogram":
                collector = Histogram(definition.name, definition.help, buckets=definition.buckets, **kwargs)
            else:
                collector = Gauge(definition.name, definition.help, **kwargs)
            self._collectors[definition.name] = collector

    def apply(self, observation: MetricObservation) -> None:
        collector = self._collectors[observation.name].labels(**observation.labels)
        if observation.operation == "snapshot":
            if observation.child_id is None:
                return
            key = (observation.name, tuple(sorted(observation.labels.items())))
            with self._lock:
                children = self._snapshots.setdefault(key, {})
                children[observation.child_id] = observation.value
                collector.set(sum(children.values()))
            return
        definition = DEFINITIONS[observation.name]
        if definition.kind == "counter":
            collector.inc(observation.value)
        elif definition.kind == "histogram":
            collector.observe(observation.value)
        else:
            collector.set(observation.value)

    def forget_child(self, child_id: str) -> None:
        with self._lock:
            for (name, label_items), children in self._snapshots.items():
                if child_id not in children:
                    continue
                children.pop(child_id, None)
                self._collectors[name].labels(**dict(label_items)).set(sum(children.values()))

    def dropped(self, reason: str) -> None:
        self._collectors["atlas_metric_observations_dropped_total"].labels(reason=reason).inc()


def start_worker_metrics_server(aggregator: WorkerMetricAggregator, *, port: int, address: str):
    return start_http_server(port, addr=address, registry=aggregator.registry)
