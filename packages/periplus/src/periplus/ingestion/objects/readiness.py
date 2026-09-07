"""A bounded, process-owned probe of the actual repository write/read/delete path."""
import asyncio
from io import BytesIO
import time
from uuid import uuid4

from periplus.ingestion.objects.store import ObjectStore

PROBE_PREFIX = "runtime/probes/"


def probe_storage(store: ObjectStore) -> None:
    key = f"{PROBE_PREFIX}{uuid4().hex}.probe"
    payload = uuid4().bytes
    try:
        if not store.put_if_absent(key, BytesIO(payload)):
            raise OSError("storage probe identity already exists")
        with store.open(key) as source:
            if source.read(len(payload) + 1) != payload:
                raise OSError("storage probe content mismatch")
    finally:
        store.delete(key)


class StorageReadiness:
    """One physical probe at a time, including after caller timeout/cancellation."""
    def __init__(self, store: ObjectStore):
        self.store = store
        self._pending: asyncio.Task | None = None
        self._valid_until = 0.0

    async def _probe(self) -> float:
        await asyncio.to_thread(probe_storage, self.store)
        return time.monotonic()

    async def check(self) -> None:
        if time.monotonic() < self._valid_until:
            return
        if self._pending is None:
            self._pending = asyncio.create_task(self._probe())
            # Consume exceptions even if every caller has timed out. Keep the task
            # until the next check observes its outcome; never detach its thread.
            self._pending.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        task = self._pending
        try:
            finished_at = await asyncio.wait_for(asyncio.shield(task), timeout=5)
        finally:
            if task.done() and self._pending is task:
                self._pending = None
        self._valid_until = finished_at + 5
        if time.monotonic() >= self._valid_until:
            await self.check()

    async def close(self) -> None:
        if self._pending is not None:
            await asyncio.gather(self._pending, return_exceptions=True)
            self._pending = None
