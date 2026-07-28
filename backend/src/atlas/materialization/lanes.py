"""Shared session-affine DuckBasin client pool for materialization work."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass

from atlas.platform.config.performance import (
    MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS,
    materialization_duckdb_memory_limit,
)
from atlas.platform.catalogue import (
    Catalogue,
    ServiceAccountTokenProvider,
    catalogue_from_env,
)
from atlas.platform.messaging.catalogue_workers import CatalogueLaneReporter


@dataclass(slots=True)
class MaterializationLane:
    """Keep one DuckBasin session on the one thread that created it."""

    catalogue: Catalogue
    executor: ThreadPoolExecutor

    @classmethod
    async def open(
        cls,
        index: int,
        *,
        tokens: ServiceAccountTokenProvider,
    ) -> MaterializationLane:
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"material-{index}",
        )
        try:
            catalogue = await asyncio.get_running_loop().run_in_executor(
                executor,
                lambda: catalogue_from_env(
                    memory_limit=materialization_duckdb_memory_limit(),
                    tokens=tokens,
                ),
            )
        except BaseException:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        return cls(catalogue=catalogue, executor=executor)

    async def call(self, operation, *args):
        future = asyncio.get_running_loop().run_in_executor(
            self.executor,
            operation,
            self.catalogue,
            *args,
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logging.critical(
                    "cancelled materialization catalogue operation %s "
                    "remained stuck for %.3fs; terminating worker",
                    getattr(operation, "__name__", type(operation).__name__),
                    MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS,
                )
                os._exit(70)
            except Exception:
                pass
            raise
        except TimeoutError:
            logging.critical(
                "materialization catalogue operation %s remained stuck "
                "for %.3fs; terminating worker",
                getattr(operation, "__name__", type(operation).__name__),
                MATERIALIZATION_CATALOGUE_HARD_TIMEOUT_SECONDS,
            )
            os._exit(70)

    async def close(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                self.executor,
                self.catalogue.close,
            )
        finally:
            self.executor.shutdown(wait=True, cancel_futures=True)


class MaterializationLanePool:
    """Lease any session-affine catalogue lane to one bounded operation."""

    def __init__(
        self,
        lanes: list[MaterializationLane],
        reporters: tuple[CatalogueLaneReporter, ...],
    ) -> None:
        if len(lanes) != len(reporters):
            raise ValueError("materialization lanes and reporters must match")
        if not lanes:
            raise ValueError("at least one materialization lane is required")
        self._lanes = lanes
        self._reporters = reporters
        self._available: asyncio.Queue[int] = asyncio.Queue()
        for index in range(len(lanes)):
            self._available.put_nowait(index)

    @property
    def capacity(self) -> int:
        return len(self._lanes)

    @asynccontextmanager
    async def acquire(self):
        index = await self._available.get()
        reporter = self._reporters[index]
        reporter.active_operation_count += 1
        try:
            yield self._lanes[index]
        finally:
            reporter.active_operation_count -= 1
            self._available.put_nowait(index)

    async def call(self, operation, *args):
        async with self.acquire() as lane:
            return await lane.call(operation, *args)

    async def call_all(self, operation, *args) -> tuple:
        """Run one metadata barrier on every session-affine client."""

        indexes = [
            await self._available.get()
            for _ in range(self.capacity)
        ]
        for index in indexes:
            self._reporters[index].active_operation_count += 1
        try:
            return tuple(
                await asyncio.gather(
                    *(
                        self._lanes[index].call(operation, *args)
                        for index in indexes
                    )
                )
            )
        finally:
            for index in indexes:
                self._reporters[index].active_operation_count -= 1
                self._available.put_nowait(index)
