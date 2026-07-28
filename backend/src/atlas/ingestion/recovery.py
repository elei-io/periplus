"""Process-local admission control for a failing ingestion dependency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
import time
from collections.abc import Callable

from atlas.platform.catalogue import DuckBasinUnavailableError


@dataclass(frozen=True, slots=True)
class DependencyAdmission:
    """Permission for one lane to use its session-affine catalogue client."""

    generation: int
    probe_required: bool


class IngestionDependencyCircuit:
    """Pause pulls during outages and restore catalogue lanes one at a time."""

    def __init__(
        self,
        lane_count: int,
        *,
        retry_initial_seconds: float = 1.0,
        retry_max_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if lane_count <= 0:
            raise ValueError("lane_count must be greater than zero")
        if retry_initial_seconds <= 0 or retry_max_seconds < retry_initial_seconds:
            raise ValueError("circuit retry bounds are invalid")
        self._lane_count = lane_count
        self._retry_initial_seconds = retry_initial_seconds
        self._retry_max_seconds = retry_max_seconds
        self._monotonic = monotonic
        self._random_value = random_value
        self._condition = asyncio.Condition()
        self._state = "closed"
        self._generation = 0
        self._consecutive_failures = 0
        self._retry_at = 0.0
        self._ready_lanes: set[int] = set(range(lane_count))
        self._probe_lane: int | None = None
        self._probe_in_flight = False

    async def admit(
        self,
        lane_index: int,
        *,
        stop: asyncio.Event,
    ) -> DependencyAdmission | None:
        """Wait until this lane may probe or process work."""

        if not 0 <= lane_index < self._lane_count:
            raise ValueError("lane_index is outside the circuit lane range")
        while not stop.is_set():
            sleep_seconds: float | None = None
            async with self._condition:
                if self._state == "closed":
                    return DependencyAdmission(self._generation, False)
                if self._state == "open":
                    remaining = self._retry_at - self._monotonic()
                    if remaining <= 0 and not self._probe_in_flight:
                        self._state = "half_open"
                        self._probe_lane = lane_index
                        self._probe_in_flight = True
                        return DependencyAdmission(self._generation, True)
                    sleep_seconds = max(0.001, remaining)
                elif lane_index in self._ready_lanes:
                    return DependencyAdmission(self._generation, False)
                elif (
                    lane_index == self._probe_lane
                    and not self._probe_in_flight
                ):
                    self._probe_in_flight = True
                    return DependencyAdmission(self._generation, True)

                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=min(1.0, sleep_seconds or 1.0),
                    )
                except TimeoutError:
                    pass
        return None

    async def unavailable(
        self,
        exc: DuckBasinUnavailableError,
        *,
        admission: DependencyAdmission | None = None,
    ) -> float:
        """Open the circuit and return the bounded delivery retry delay."""

        async with self._condition:
            if (
                admission is not None
                and admission.generation != self._generation
            ):
                return min(
                    self._retry_max_seconds,
                    max(0.001, self._retry_at - self._monotonic()),
                )
            self._generation += 1
            self._consecutive_failures += 1
            exponential = min(
                self._retry_max_seconds,
                self._retry_initial_seconds
                * (2 ** min(10, self._consecutive_failures - 1)),
            )
            jittered = exponential * (0.8 + 0.4 * self._random_value())
            delay = min(
                self._retry_max_seconds,
                max(jittered, exc.retry_after_seconds or 0.0),
            )
            self._state = "open"
            self._retry_at = self._monotonic() + delay
            self._ready_lanes.clear()
            self._probe_lane = None
            self._probe_in_flight = False
            self._condition.notify_all()
            return delay

    async def recovered(self, admission: DependencyAdmission, lane_index: int) -> None:
        """Accept a successful probe and admit the next lane."""

        if not admission.probe_required:
            return
        async with self._condition:
            if admission.generation != self._generation:
                return
            if lane_index != self._probe_lane or not self._probe_in_flight:
                return
            self._ready_lanes.add(lane_index)
            self._probe_in_flight = False
            remaining = [
                lane
                for lane in range(self._lane_count)
                if lane not in self._ready_lanes
            ]
            if not remaining:
                self._state = "closed"
                self._probe_lane = None
                self._consecutive_failures = 0
            else:
                self._state = "recovering"
                self._probe_lane = remaining[0]
            self._condition.notify_all()

    async def is_current(self, admission: DependencyAdmission) -> bool:
        """Return whether an earlier admission still belongs to this circuit epoch."""

        async with self._condition:
            return admission.generation == self._generation

    async def is_closed(self) -> bool:
        """Return whether ordinary health traffic may use the dependency."""

        async with self._condition:
            return self._state == "closed"

    async def state(self) -> str:
        """Return the current circuit state for lane telemetry."""

        async with self._condition:
            return self._state

    async def retry_delay(self) -> float:
        """Return a bounded delay suitable for NAKing uncommitted work."""

        async with self._condition:
            if self._state == "closed":
                return self._retry_initial_seconds
            return min(
                self._retry_max_seconds,
                max(
                    self._retry_initial_seconds,
                    self._retry_at - self._monotonic(),
                ),
            )
