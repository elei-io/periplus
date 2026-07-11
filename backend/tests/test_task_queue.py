import unittest
from unittest.mock import AsyncMock

from nats.js.errors import NoKeysError

from runtime.task_queue import list_runs


class TaskQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_run_bucket_lists_no_runs(self) -> None:
        bucket = AsyncMock()
        bucket.keys.side_effect = NoKeysError()

        self.assertEqual(await list_runs(bucket), [])


if __name__ == "__main__":
    unittest.main()
