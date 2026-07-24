from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from repository.ingestion.health import HealthMonitor
from runtime.catalogue_workers import (
    CatalogueLaneReporter,
    CatalogueLaneState,
    CatalogueWorkerState,
    catalogue_worker_presence,
    list_catalogue_worker_states,
    monitor_catalogue_lanes,
)


class CatalogueWorkerPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_executor_capacity_separately_from_permits(self) -> None:
        states = {
            "ingestion-one": CatalogueWorkerState(
                worker_id="ingestion:one",
                capability="ingestion",
                started_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                lanes=(
                    CatalogueLaneState(
                        lane_index=0, status="active", active=True
                    ),
                ),
                process_ready=True,
            ),
            "ingestion-two": CatalogueWorkerState(
                worker_id="ingestion:two",
                capability="ingestion",
                started_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                lanes=(
                    CatalogueLaneState(
                        lane_index=0, status="available", active=False
                    ),
                ),
                process_ready=True,
            ),
        }

        class Bucket:
            async def keys(self):
                return list(states)

            async def get(self, key):
                return SimpleNamespace(value=states[key].model_dump_json().encode())

        workers = await list_catalogue_worker_states(Bucket())
        self.assertEqual(len(workers), 2)
        self.assertEqual(sum(worker.usable_capacity for worker in workers), 2)
        self.assertEqual(sum(worker.active_operation_count for worker in workers), 1)

    async def test_presence_marks_health_ready_after_a_publish(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()

        class Bucket:
            async def put(self, _key, _value):
                stop.set()

        await catalogue_worker_presence(
            Bucket(),
            worker_id="ingestion:one",
            capability="ingestion",
            started_at=datetime.now(UTC),
            lanes=lambda: (
                CatalogueLaneState(
                    lane_index=0, status="available", active=False
                ),
            ),
            process_health=lambda: (True, "ready"),
            stop=stop,
            monitor=monitor,
        )

        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_presence_marks_health_unavailable_after_a_publish_failure(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()

        class Bucket:
            async def put(self, _key, _value):
                stop.set()
                raise PermissionError("KV publish denied")

        await catalogue_worker_presence(
            Bucket(),
            worker_id="materialization:one",
            capability="materialization",
            started_at=datetime.now(UTC),
            lanes=lambda: (
                CatalogueLaneState(
                    lane_index=0, status="available", active=False
                ),
            ),
            process_health=lambda: (True, "ready"),
            stop=stop,
            monitor=monitor,
        )

        ready, detail = monitor.status()
        self.assertFalse(ready)
        self.assertEqual(
            detail,
            "catalogue_worker_presence: KV publish denied",
        )

    def test_lane_failure_reduces_usable_capacity_without_disappearing(self) -> None:
        monitors = [HealthMonitor(), HealthMonitor()]
        lanes = [
            CatalogueLaneReporter(lane_index=index)
            for index in range(len(monitors))
        ]
        for lane, monitor in zip(lanes, monitors, strict=True):
            monitor.dependencies_ready()
            lane.attach(lambda monitor=monitor: monitor.status(include_liveness=False))

        monitors[1].dependencies_unavailable("catalogue probe timed out")
        states = tuple(lane.snapshot() for lane in lanes)
        worker = CatalogueWorkerState(
            worker_id="ingestion:one",
            capability="ingestion",
            started_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            lanes=states,
            process_ready=True,
        )

        self.assertEqual(worker.configured_capacity, 2)
        self.assertEqual(worker.usable_capacity, 1)
        self.assertTrue(worker.healthy)
        self.assertEqual(states[1].detail, "catalogue probe timed out")

    def test_total_lane_failure_retains_configured_capacity(self) -> None:
        lane = CatalogueLaneReporter(lane_index=0)
        monitor = HealthMonitor()
        monitor.dependencies_unavailable("catalogue unavailable")
        lane.attach(lambda: monitor.status(include_liveness=False))
        worker = CatalogueWorkerState(
            worker_id="ingestion:one",
            capability="ingestion",
            started_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            lanes=(lane.snapshot(),),
            process_ready=True,
        )

        self.assertEqual(worker.configured_capacity, 1)
        self.assertEqual(worker.usable_capacity, 0)
        self.assertFalse(worker.healthy)

    async def test_process_readiness_requires_one_usable_lane(self) -> None:
        stop = asyncio.Event()
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        lane = CatalogueLaneReporter(lane_index=0)
        lane_monitor = HealthMonitor()
        lane_monitor.dependencies_unavailable("catalogue unavailable")
        lane.attach(
            lambda: lane_monitor.status(include_liveness=False)
        )

        task = asyncio.create_task(
            monitor_catalogue_lanes(
                lane_reporters=(lane,),
                stop=stop,
                monitor=monitor,
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(monitor.status()[0])

        lane_monitor.dependencies_ready()
        await asyncio.sleep(1.01)
        self.assertTrue(monitor.status()[0])
        stop.set()
        await task


if __name__ == "__main__":
    unittest.main()
