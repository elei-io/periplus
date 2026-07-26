from __future__ import annotations

import asyncio
import unittest

from repository.catalogue import DuckBasinUnavailableError
from repository.ingestion.recovery import IngestionDependencyCircuit


class IngestionDependencyCircuitTests(unittest.IsolatedAsyncioTestCase):
    async def test_outage_allows_one_probe_then_restores_lanes_in_order(self) -> None:
        now = [10.0]
        circuit = IngestionDependencyCircuit(
            3,
            retry_initial_seconds=2,
            retry_max_seconds=10,
            monotonic=lambda: now[0],
            random_value=lambda: 0.5,
        )
        stop = asyncio.Event()

        delay = await circuit.unavailable(
            DuckBasinUnavailableError("down")
        )
        self.assertEqual(delay, 2)
        self.assertEqual(await circuit.state(), "open")

        now[0] = 12.0
        first = await circuit.admit(2, stop=stop)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertTrue(first.probe_required)
        self.assertEqual(await circuit.state(), "half_open")

        blocked = asyncio.create_task(circuit.admit(1, stop=stop))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())

        await circuit.recovered(first, 2)
        self.assertEqual(await circuit.state(), "recovering")
        ready = await circuit.admit(2, stop=stop)
        self.assertIsNotNone(ready)
        assert ready is not None
        self.assertFalse(ready.probe_required)

        lane_zero = await circuit.admit(0, stop=stop)
        self.assertIsNotNone(lane_zero)
        assert lane_zero is not None
        self.assertTrue(lane_zero.probe_required)
        await circuit.recovered(lane_zero, 0)

        lane_one = await blocked
        self.assertIsNotNone(lane_one)
        assert lane_one is not None
        self.assertTrue(lane_one.probe_required)
        await circuit.recovered(lane_one, 1)
        self.assertEqual(await circuit.state(), "closed")

        for lane in range(3):
            admission = await circuit.admit(lane, stop=stop)
            self.assertIsNotNone(admission)
            assert admission is not None
            self.assertFalse(admission.probe_required)

    async def test_retry_after_is_bounded_for_nak_delay(self) -> None:
        circuit = IngestionDependencyCircuit(
            1,
            retry_initial_seconds=1,
            retry_max_seconds=30,
            random_value=lambda: 0.5,
        )

        delay = await circuit.unavailable(
            DuckBasinUnavailableError(
                "busy",
                retry_after_seconds=300,
            )
        )

        self.assertEqual(delay, 30)

    async def test_stale_lane_failures_do_not_multiply_backoff(self) -> None:
        now = [0.0]
        circuit = IngestionDependencyCircuit(
            2,
            retry_initial_seconds=2,
            retry_max_seconds=30,
            monotonic=lambda: now[0],
            random_value=lambda: 0.5,
        )
        stop = asyncio.Event()
        first = await circuit.admit(0, stop=stop)
        stale = await circuit.admit(1, stop=stop)
        assert first is not None and stale is not None

        first_delay = await circuit.unavailable(
            DuckBasinUnavailableError("down"),
            admission=first,
        )
        stale_delay = await circuit.unavailable(
            DuckBasinUnavailableError("same outage"),
            admission=stale,
        )

        self.assertEqual(first_delay, 2)
        self.assertEqual(stale_delay, 2)


if __name__ == "__main__":
    unittest.main()
