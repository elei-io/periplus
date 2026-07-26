from __future__ import annotations

import asyncio
import threading
import unittest

from runtime.catalogue_lane import catalogue_operation_lane


class CatalogueLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalogue_operations_are_serialized_within_one_process(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        order: list[str] = []

        def first() -> None:
            order.append("first-start")
            first_started.set()
            release_first.wait(timeout=2)
            order.append("first-end")

        def second() -> None:
            order.append("second")

        async def run(function) -> None:
            async with catalogue_operation_lane():
                await asyncio.to_thread(function)

        first_task = asyncio.create_task(run(first))
        await asyncio.to_thread(first_started.wait, 2)
        second_task = asyncio.create_task(run(second))
        await asyncio.sleep(0)
        self.assertEqual(order, ["first-start"])
        release_first.set()
        await asyncio.gather(first_task, second_task)

        self.assertEqual(order, ["first-start", "first-end", "second"])


if __name__ == "__main__":
    unittest.main()
