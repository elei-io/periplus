import asyncio
import time
import unittest
from contextlib import asynccontextmanager
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
from atlas.materialization.lanes import MaterializationLanePool
from atlas.materialization.pipeline import (
    BoundedStagePlan,
    StageSelection,
    _partition,
    execute_bounded_stage,
)


@asynccontextmanager
async def _lease(*_args, **_kwargs):
    yield


class _Lane:
    async def call(self, operation, *args):
        return await asyncio.to_thread(operation, SimpleNamespace(), *args)


class MaterializationPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_partition_honors_item_and_byte_budgets(self) -> None:
        self.assertEqual(
            _partition(
                (4, 7, 20, 2),
                source_bytes=lambda value: value,
                item_budget=2,
                byte_budget=10,
            ),
            [(4,), (7,), (20,), (2,)],
        )

    def test_stable_key_groups_are_packed_to_the_byte_budget(self) -> None:
        self.assertEqual(
            _partition(
                (1, 2, 3, 4, 5, 6),
                source_bytes=lambda _value: 10,
                item_budget=3,
                byte_budget=40,
                partition_key=lambda value: value % 2,
            ),
            [(2, 4, 6), (1, 3, 5)],
        )

    async def test_projection_is_parallel_and_writes_are_serialized(self) -> None:
        lock = Lock()
        active_projectors = 0
        max_projectors = 0
        active_writers = 0
        max_writers = 0
        written: list[int] = []

        def project(items):
            nonlocal active_projectors, max_projectors
            with lock:
                active_projectors += 1
                max_projectors = max(max_projectors, active_projectors)
            time.sleep(0.02)
            with lock:
                active_projectors -= 1
            return items[0]

        def write(_catalogue, output):
            nonlocal active_writers, max_writers
            with lock:
                active_writers += 1
                max_writers = max(max_writers, active_writers)
            time.sleep(0.005)
            written.append(output)
            with lock:
                active_writers -= 1
            return 1

        reporters = tuple(
            SimpleNamespace(active_operation_count=0) for _ in range(8)
        )
        pool = MaterializationLanePool(
            [_Lane() for _ in range(8)],
            reporters,
        )
        plan = BoundedStagePlan(
            name="example",
            target="example",
            select=lambda _catalogue: StageSelection(
                items=tuple(range(12))
            ),
            project=project,
            write=write,
            source_bytes=lambda _item: 1,
            item_budget=1,
            byte_budget=1,
            parallelism=8,
        )

        with patch("atlas.materialization.pipeline.operation_leases", _lease):
            result = await execute_bounded_stage(
                SimpleNamespace(),
                pool,
                plan,
            )

        self.assertGreaterEqual(max_projectors, 2)
        self.assertEqual(max_writers, 1)
        self.assertCountEqual(written, range(12))
        self.assertEqual(result.source_items, 12)
        self.assertEqual(result.partitions, 12)
        self.assertEqual(result.output_rows, 12)
        self.assertEqual(
            [reporter.active_operation_count for reporter in reporters],
            [0] * 8,
        )

    async def test_partition_writer_retries_transaction_conflict(self) -> None:
        attempts = 0

        def write(_catalogue, _output):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise duckdb.TransactionException("conflict")
            return 3

        reporters = tuple(
            SimpleNamespace(active_operation_count=0) for _ in range(2)
        )
        pool = MaterializationLanePool(
            [_Lane() for _ in range(2)],
            reporters,
        )
        plan = BoundedStagePlan(
            name="retry",
            target="retry",
            select=lambda _catalogue: StageSelection(items=("source",)),
            project=lambda _items: "output",
            write=write,
            source_bytes=lambda _item: 1,
            item_budget=1,
            byte_budget=1,
            parallelism=1,
        )

        with (
            patch("atlas.materialization.pipeline.operation_leases", _lease),
            patch("atlas.materialization.pipeline.time.sleep"),
        ):
            result = await execute_bounded_stage(
                SimpleNamespace(),
                pool,
                plan,
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(result.output_rows, 3)

    async def test_stage_can_use_all_bounded_writer_lanes(self) -> None:
        lock = Lock()
        active_writers = 0
        max_writers = 0

        def write(_catalogue, _output):
            nonlocal active_writers, max_writers
            with lock:
                active_writers += 1
                max_writers = max(max_writers, active_writers)
            time.sleep(0.02)
            with lock:
                active_writers -= 1
            return 1

        reporters = tuple(
            SimpleNamespace(active_operation_count=0) for _ in range(8)
        )
        pool = MaterializationLanePool(
            [_Lane() for _ in range(8)],
            reporters,
        )
        plan = BoundedStagePlan(
            name="parallel-writes",
            target="parallel-writes",
            select=lambda _catalogue: StageSelection(
                items=("source",)
            ),
            project=lambda _items: "projected",
            write=write,
            source_bytes=lambda _item: 1,
            item_budget=1,
            byte_budget=1,
            parallelism=8,
            writer_parallelism=8,
            partition_output=lambda _output: tuple(range(8)),
            output_bytes=lambda _output: 16,
        )

        with patch("atlas.materialization.pipeline.operation_leases", _lease):
            result = await execute_bounded_stage(
                SimpleNamespace(),
                pool,
                plan,
            )

        self.assertEqual(max_writers, 8)
        self.assertEqual(result.partitions, 1)
        self.assertEqual(result.write_partitions, 8)
        self.assertEqual(result.output_rows, 8)
        self.assertEqual(result.output_bytes, 128)


if __name__ == "__main__":
    unittest.main()
