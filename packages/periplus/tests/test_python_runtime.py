"""Runtime regression: completed captures must not be held by a transport sentinel."""
import asyncio
import gc
import unittest
import weakref


class CapturePayload:
    def __init__(self):
        self.html = bytearray(32 * 1024)


class AsyncWaitOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_results_released_while_transport_remains_open(self):
        # Playwright races every protocol reply against one process-lived error
        # future. CPython gh-152569 left completed callers in its await graph.
        transport_error = asyncio.get_running_loop().create_future()
        payloads = []

        async def capture():
            payload = CapturePayload()
            payloads.append(weakref.ref(payload))
            reply = asyncio.create_task(asyncio.sleep(0))
            await asyncio.wait({reply, transport_error}, return_when=asyncio.FIRST_COMPLETED)
            return payload

        try:
            for _ in range(100):
                await asyncio.create_task(capture())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            gc.collect()
            self.assertFalse(transport_error.done())
            self.assertEqual(sum(ref() is not None for ref in payloads), 0)
        finally:
            transport_error.cancel()


if __name__ == "__main__":
    unittest.main()
