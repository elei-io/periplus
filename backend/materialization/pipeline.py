"""Bounded local projection with independently bounded catalogue writers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from observability import materialization_metrics
from repository.catalogue import Catalogue
from repository.catalogue.operations import run_with_catalogue_retry
from runtime.operation_leases import operation_leases

from materialization.lanes import MaterializationLanePool

_Source = TypeVar("_Source")
_Output = TypeVar("_Output")


@dataclass(frozen=True, slots=True)
class StageSelection(Generic[_Source, _Output]):
    """Selected source items plus target mutations that need no projection."""

    items: tuple[_Source, ...]
    initial_outputs: tuple[_Output, ...] = ()
    cursor: str | None = None
    done: bool = True


@dataclass(frozen=True, slots=True)
class BoundedStagePlan(Generic[_Source, _Output]):
    """Developer contract for a bounded materialization stage."""

    name: str
    target: str
    select: Callable[..., StageSelection[_Source, _Output]]
    project: Callable[[tuple[_Source, ...]], _Output]
    write: Callable[[Catalogue, _Output], int]
    source_bytes: Callable[[_Source], int]
    item_budget: int
    byte_budget: int
    parallelism: int
    additional_targets: tuple[str, ...] = ()
    writer_parallelism: int = 1
    output_bytes: Callable[[_Output], int] = lambda _output: 0
    combine_outputs: (
        Callable[[tuple[_Output, ...]], _Output] | None
    ) = None
    partition_output: Callable[[_Output], tuple[_Output, ...]] = (
        lambda output: (output,)
    )
    partition_key: Callable[[_Source], object] | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.target:
            raise ValueError("bounded stage name and target are required")
        if any(not target for target in self.additional_targets):
            raise ValueError("bounded stage additional targets must be named")
        if self.target in self.additional_targets:
            raise ValueError("bounded stage targets must be distinct")
        if (
            self.item_budget < 1
            or self.byte_budget < 1
            or self.parallelism < 1
            or self.writer_parallelism < 1
        ):
            raise ValueError("bounded stage budgets must be positive")


@dataclass(frozen=True, slots=True)
class StageExecution:
    source_items: int
    source_bytes: int
    partitions: int
    write_partitions: int
    output_rows: int
    select_seconds: float
    project_seconds: float
    write_seconds: float
    elapsed_seconds: float
    output_bytes: int = 0
    cursor: str | None = None
    done: bool = True


async def execute_bounded_stage(
    leases,
    lane_pool: MaterializationLanePool,
    plan: BoundedStagePlan[_Source, _Output],
    *select_args: Any,
) -> StageExecution:
    """Execute a stage with bounded local projection and catalogue writes."""

    started = time.perf_counter()
    async with operation_leases(
        leases,
        tuple(
            f"material.{target}"
            for target in (plan.target, *plan.additional_targets)
        ),
        phase="materialization",
        acquire_timeout=0,
    ):
        selected_at = time.perf_counter()
        selection = await lane_pool.call(
            _select,
            plan,
            select_args,
        )
        select_seconds = time.perf_counter() - selected_at
        partitions = tuple(
            _partition(
                selection.items,
                source_bytes=plan.source_bytes,
                item_budget=plan.item_budget,
                byte_budget=plan.byte_budget,
                partition_key=plan.partition_key,
            )
        )
        source_bytes = sum(
            max(0, int(plan.source_bytes(item)))
            for item in selection.items
        )
        output_rows = 0
        output_bytes = 0
        project_seconds = 0.0
        write_seconds = 0.0
        write_partitions = 0
        for output in selection.initial_outputs:
            rows, seconds = await lane_pool.call(_write, plan, output)
            output_rows += rows
            output_bytes += max(0, int(plan.output_bytes(output)))
            write_seconds += seconds
            write_partitions += 1
        if partitions:
            (
                projected_rows,
                projected_bytes,
                projected_time,
                written_time,
                projected_partitions,
            ) = await _project_and_write(
                lane_pool,
                plan,
                partitions,
            )
            output_rows += projected_rows
            output_bytes += projected_bytes
            project_seconds += projected_time
            write_seconds += written_time
            write_partitions += projected_partitions
    result = StageExecution(
        source_items=len(selection.items),
        source_bytes=source_bytes,
        partitions=len(partitions),
        write_partitions=write_partitions,
        output_rows=output_rows,
        output_bytes=output_bytes,
        select_seconds=select_seconds,
        project_seconds=project_seconds,
        write_seconds=write_seconds,
        elapsed_seconds=time.perf_counter() - started,
        cursor=selection.cursor,
        done=selection.done,
    )
    materialization_metrics.stage(
        workload=plan.name,
        source_items=result.source_items,
        source_bytes=result.source_bytes,
        output_rows=result.output_rows,
        projection_bytes=result.output_bytes,
        select_seconds=result.select_seconds,
        project_seconds=result.project_seconds,
        write_seconds=result.write_seconds,
        elapsed_seconds=result.elapsed_seconds,
    )
    logging.info(
        "bounded materialization stage %s targets=%s "
        "items=%s bytes=%s source_partitions=%s write_partitions=%s "
        "rows=%s output_bytes=%s "
        "select=%.3fs project=%.3fs write=%.3fs elapsed=%.3fs",
        plan.name,
        ",".join(
            f"material.{target}"
            for target in (plan.target, *plan.additional_targets)
        ),
        result.source_items,
        result.source_bytes,
        result.partitions,
        result.write_partitions,
        result.output_rows,
        result.output_bytes,
        result.select_seconds,
        result.project_seconds,
        result.write_seconds,
        result.elapsed_seconds,
    )
    return result


async def _project_and_write(
    lane_pool: MaterializationLanePool,
    plan: BoundedStagePlan[_Source, _Output],
    partitions: tuple[tuple[_Source, ...], ...],
) -> tuple[int, int, float, float, int]:
    parallelism = min(
        plan.parallelism,
        len(partitions),
    )
    writer_parallelism = min(
        plan.writer_parallelism,
        lane_pool.capacity,
    )
    project_slots = asyncio.Semaphore(parallelism)
    next_partition = 0
    next_partition_lock = asyncio.Lock()
    projected_outputs: list[tuple[int, _Output]] = []
    write_results: list[tuple[int, int, float]] = []
    project_started = time.perf_counter()

    async def projector() -> None:
        nonlocal next_partition
        while True:
            async with next_partition_lock:
                if next_partition >= len(partitions):
                    break
                partition_index = next_partition
                partition = partitions[partition_index]
                next_partition += 1
            async with project_slots:
                output = await asyncio.to_thread(
                    _project,
                    plan,
                    partition,
                )
            projected_outputs.append(
                (partition_index, output)
            )

    async with asyncio.TaskGroup() as tasks:
        for _ in range(parallelism):
            tasks.create_task(projector())
    ordered_outputs = [
        output
        for _index, output in sorted(
            projected_outputs,
            key=lambda item: item[0],
        )
    ]
    if plan.combine_outputs is not None and ordered_outputs:
        ordered_outputs = [
            await asyncio.to_thread(
                plan.combine_outputs,
                tuple(ordered_outputs),
            )
        ]
    project_seconds = time.perf_counter() - project_started
    outputs = tuple(
        partitioned
        for output in ordered_outputs
        for partitioned in plan.partition_output(output)
    )
    if not outputs:
        raise RuntimeError(
            f"materialization stage {plan.name} discarded its output"
        )
    next_output = 0
    next_output_lock = asyncio.Lock()
    write_started = time.perf_counter()

    async def writer() -> None:
        nonlocal next_output
        while True:
            async with next_output_lock:
                if next_output >= len(outputs):
                    return
                output = outputs[next_output]
                next_output += 1
            rows, seconds = await lane_pool.call(
                _write,
                plan,
                output,
            )
            write_results.append(
                (
                    rows,
                    max(0, int(plan.output_bytes(output))),
                    seconds,
                )
            )

    async with asyncio.TaskGroup() as tasks:
        for _ in range(min(writer_parallelism, len(outputs))):
            tasks.create_task(writer())
    write_seconds = time.perf_counter() - write_started

    output_rows = sum(rows for rows, _bytes, _seconds in write_results)
    output_bytes = sum(size for _rows, size, _seconds in write_results)
    logging.info(
        "bounded materialization stage %s wrote %s output partitions",
        plan.name,
        len(write_results),
    )
    return (
        output_rows,
        output_bytes,
        project_seconds,
        write_seconds,
        len(write_results),
    )


def _keyed_groups(
    items: tuple[_Source, ...],
    *,
    source_bytes: Callable[[_Source], int],
    partition_key: Callable[[_Source], object],
) -> list[tuple[object, tuple[_Source, ...], int]]:
    groups: dict[object, list[_Source]] = {}
    for item in items:
        groups.setdefault(partition_key(item), []).append(item)
    return [
        (
            key,
            tuple(group),
            sum(max(0, int(source_bytes(item))) for item in group),
        )
        for key, group in sorted(groups.items(), key=lambda pair: str(pair[0]))
    ]


def _select(
    catalogue: Catalogue,
    plan: BoundedStagePlan[_Source, _Output],
    args: tuple[Any, ...],
) -> StageSelection[_Source, _Output]:
    return plan.select(catalogue, *args)


def _project(
    plan: BoundedStagePlan[_Source, _Output],
    partition: tuple[_Source, ...],
) -> _Output:
    return plan.project(partition)


def _write(
    catalogue: Catalogue,
    plan: BoundedStagePlan[_Source, _Output],
    output: _Output,
) -> tuple[int, float]:
    started = time.perf_counter()
    rows = run_with_catalogue_retry(
        lambda: plan.write(catalogue, output),
        description=f"materialization stage {plan.name} partition",
    )
    return rows, time.perf_counter() - started


def _partition(
    items: tuple[_Source, ...],
    *,
    source_bytes: Callable[[_Source], int],
    item_budget: int,
    byte_budget: int,
    partition_key: Callable[[_Source], object] | None = None,
) -> list[tuple[_Source, ...]]:
    if partition_key is not None:
        groups = _keyed_groups(
            items,
            source_bytes=source_bytes,
            partition_key=partition_key,
        )
        partitions: list[tuple[_Source, ...]] = []
        current: list[_Source] = []
        current_bytes = 0
        for _key, group, group_bytes in groups:
            if current and (
                len(current) + len(group) > item_budget
                or current_bytes + group_bytes > byte_budget
            ):
                partitions.append(tuple(current))
                current = []
                current_bytes = 0
            if len(group) > item_budget or group_bytes > byte_budget:
                if current:
                    partitions.append(tuple(current))
                    current = []
                    current_bytes = 0
                partitions.extend(
                    _partition(
                        group,
                        source_bytes=source_bytes,
                        item_budget=item_budget,
                        byte_budget=byte_budget,
                    )
                )
                continue
            current.extend(group)
            current_bytes += group_bytes
        if current:
            partitions.append(tuple(current))
        return partitions
    partitions: list[tuple[_Source, ...]] = []
    current: list[_Source] = []
    current_bytes = 0
    for item in items:
        item_bytes = max(0, int(source_bytes(item)))
        if current and (
            len(current) >= item_budget
            or current_bytes + item_bytes > byte_budget
        ):
            partitions.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += item_bytes
    if current:
        partitions.append(tuple(current))
    return partitions
