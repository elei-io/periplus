"""Bounded parallel projection with one serialized materialization writer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any, Generic, TypeVar

from materialization.lanes import MaterializationLanePool
from repository.catalogue import Catalogue
from repository.catalogue.operations import run_with_catalogue_retry
from runtime.operation_leases import operation_leases


_Source = TypeVar("_Source")
_Output = TypeVar("_Output")


@dataclass(frozen=True, slots=True)
class StageSelection(Generic[_Source, _Output]):
    """Selected source items plus target mutations that need no projection."""

    items: tuple[_Source, ...]
    initial_outputs: tuple[_Output, ...] = ()


@dataclass(frozen=True, slots=True)
class BoundedStagePlan(Generic[_Source, _Output]):
    """Developer contract for a bounded materialization stage."""

    name: str
    target: str
    select: Callable[..., StageSelection[_Source, _Output]]
    project: Callable[[Catalogue, tuple[_Source, ...]], _Output]
    write: Callable[[Catalogue, _Output], int]
    source_bytes: Callable[[_Source], int]
    item_budget: int
    byte_budget: int
    parallelism: int
    additional_targets: tuple[str, ...] = ()

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
        ):
            raise ValueError("bounded stage budgets must be positive")


@dataclass(frozen=True, slots=True)
class StageExecution:
    source_items: int
    source_bytes: int
    partitions: int
    output_rows: int
    select_seconds: float
    project_seconds: float
    write_seconds: float
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class _Projected(Generic[_Output]):
    output: _Output
    seconds: float


async def execute_bounded_stage(
    leases,
    lane_pool: MaterializationLanePool,
    plan: BoundedStagePlan[_Source, _Output],
    *select_args: Any,
) -> StageExecution:
    """Execute a stage with bounded in-flight output and one target writer."""

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
            )
        )
        source_bytes = sum(
            max(0, int(plan.source_bytes(item)))
            for item in selection.items
        )
        output_rows = 0
        project_seconds = 0.0
        write_seconds = 0.0
        for output in selection.initial_outputs:
            rows, seconds = await lane_pool.call(_write, plan, output)
            output_rows += rows
            write_seconds += seconds
        if partitions:
            projected_rows, projected_time, written_time = (
                await _project_and_write(
                    lane_pool,
                    plan,
                    partitions,
                )
            )
            output_rows += projected_rows
            project_seconds += projected_time
            write_seconds += written_time
    result = StageExecution(
        source_items=len(selection.items),
        source_bytes=source_bytes,
        partitions=len(partitions),
        output_rows=output_rows,
        select_seconds=select_seconds,
        project_seconds=project_seconds,
        write_seconds=write_seconds,
        elapsed_seconds=time.perf_counter() - started,
    )
    logging.info(
        "bounded materialization stage %s targets=%s "
        "items=%s bytes=%s partitions=%s rows=%s "
        "select=%.3fs project=%.3fs write=%.3fs elapsed=%.3fs",
        plan.name,
        ",".join(
            f"material.{target}"
            for target in (plan.target, *plan.additional_targets)
        ),
        result.source_items,
        result.source_bytes,
        result.partitions,
        result.output_rows,
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
) -> tuple[int, float, float]:
    parallelism = min(
        plan.parallelism,
        max(1, lane_pool.capacity - 1),
        len(partitions),
    )
    pending: set[asyncio.Task[_Projected[_Output]]] = set()
    next_partition = 0
    output_rows = 0
    project_seconds = 0.0
    write_seconds = 0.0
    written_partitions = 0
    progress_interval = max(1, len(partitions) // 10)

    def schedule(slots: int | None = None) -> None:
        nonlocal next_partition
        remaining = (
            parallelism - len(pending)
            if slots is None
            else min(slots, parallelism - len(pending))
        )
        while (
            next_partition < len(partitions)
            and remaining > 0
        ):
            partition = partitions[next_partition]
            next_partition += 1
            pending.add(
                asyncio.create_task(
                    lane_pool.call(_project, plan, partition)
                )
            )
            remaining -= 1

    schedule()
    while pending:
        done, pending = await asyncio.wait(
            pending,
            return_when=asyncio.FIRST_COMPLETED,
        )
        completed: list[_Projected[_Output]] = []
        failure: BaseException | None = None
        for task in done:
            try:
                completed.append(task.result())
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise failure
        try:
            for index, projected in enumerate(completed):
                project_seconds += projected.seconds
                rows, seconds = await lane_pool.call(
                    _write,
                    plan,
                    projected.output,
                )
                output_rows += rows
                write_seconds += seconds
                written_partitions += 1
                if (
                    written_partitions == len(partitions)
                    or written_partitions % progress_interval == 0
                ):
                    logging.info(
                        "bounded materialization stage %s progress "
                        "partitions=%s/%s rows=%s",
                        plan.name,
                        written_partitions,
                        len(partitions),
                        output_rows,
                    )
                unconsumed_outputs = len(completed) - index - 1
                free_slots = (
                    parallelism - len(pending) - unconsumed_outputs
                )
                if free_slots > 0:
                    schedule(free_slots)
        except BaseException:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
    return output_rows, project_seconds, write_seconds


def _select(
    catalogue: Catalogue,
    plan: BoundedStagePlan[_Source, _Output],
    args: tuple[Any, ...],
) -> StageSelection[_Source, _Output]:
    return plan.select(catalogue, *args)


def _project(
    catalogue: Catalogue,
    plan: BoundedStagePlan[_Source, _Output],
    partition: tuple[_Source, ...],
) -> _Projected[_Output]:
    started = time.perf_counter()
    return _Projected(
        output=plan.project(catalogue, partition),
        seconds=time.perf_counter() - started,
    )


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
) -> list[tuple[_Source, ...]]:
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
