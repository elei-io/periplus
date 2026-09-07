import asyncio
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from periplus.crawl.control.collections.history import CollectionHistory, HistoryUnavailable


class CollectionHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_history_is_bounded_and_cancelled_reader_drains_before_releasing_slot(self):
        entered, release = asyncio.Event(), asyncio.Event()
        control = AsyncMock()
        async def run(operation):
            entered.set()
            await release.wait()
            return None
        control.run.side_effect = run
        history = CollectionHistory(control)
        reading = asyncio.create_task(history.get(uuid4(), public_only=True))
        await entered.wait()
        with self.assertRaises(HistoryUnavailable):
            await history.get(uuid4(), public_only=False)
        reading.cancel()
        await asyncio.sleep(0)
        self.assertTrue(history.slot.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await reading
        self.assertFalse(history.slot.locked())
        control.run.assert_awaited_once()

    async def test_overlapping_page_reads_serialize_with_bounded_admission(self):
        entered, release = asyncio.Event(), asyncio.Event()
        control = AsyncMock()
        active = maximum_active = 0
        async def run(operation):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            entered.set()
            await release.wait()
            active -= 1
            return None
        control.run.side_effect = run
        history = CollectionHistory(control)
        tasks = [asyncio.create_task(history.get(uuid4(), public_only=True)) for _ in range(8)]
        await entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(history.pending_reads, 8)
        with self.assertRaises(HistoryUnavailable):
            await history.get(uuid4(), public_only=True)
        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(maximum_active, 1)
        self.assertEqual(history.pending_reads, 0)
        self.assertEqual(control.run.await_count, 8)

    async def test_cancelled_waiter_does_not_release_the_active_reader(self):
        entered, release = asyncio.Event(), asyncio.Event()
        control = AsyncMock()
        async def run(operation):
            entered.set()
            await release.wait()
        control.run.side_effect = run
        history = CollectionHistory(control)
        active = asyncio.create_task(history.get(uuid4(), public_only=True))
        await entered.wait()
        queued = asyncio.create_task(history.get(uuid4(), public_only=True))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertTrue(history.slot.locked())
        self.assertEqual(history.pending_reads, 1)
        release.set()
        await active
        self.assertEqual(history.pending_reads, 0)

    async def test_storage_failure_is_not_an_absent_identity(self):
        control = AsyncMock()
        control.run.side_effect = RuntimeError("lake unavailable")
        with self.assertRaises(HistoryUnavailable):
            await CollectionHistory(control).get(uuid4(), public_only=False)

    async def test_listing_shares_read_slot_and_invalid_cursor_does_not_use_catalogue(self):
        from periplus.crawl.control.collections.history import HistoryCursor, decode_cursor
        import base64
        from datetime import datetime
        control = AsyncMock()
        history = CollectionHistory(control)
        for cursor in ("!", "x" * 513, base64.urlsafe_b64encode(
                HistoryCursor(requested_at=datetime(2026, 1, 1), id=uuid4()).model_dump_json().encode()).decode()):
            with self.assertRaises(ValueError):
                decode_cursor(cursor)
            with self.assertRaises(ValueError):
                await history.list(public_only=True, limit=20, cursor=cursor)
        control.run.assert_not_awaited()
        async with history.slot:
            with self.assertRaises(HistoryUnavailable):
                await history.list(public_only=True, limit=20, cursor=None)

    async def test_arrival_reads_share_the_slot_and_validate_cursor_scope_before_io(self):
        import base64
        from datetime import UTC, datetime
        from periplus.crawl.control.collections.arrivals import ArrivalCursor
        control = AsyncMock()
        history = CollectionHistory(control)
        identity = uuid4()
        wrong = ArrivalCursor(collection_id=uuid4(), fulfillment_id=uuid4(), decided_at=datetime.now(UTC))
        cursor = base64.urlsafe_b64encode(wrong.model_dump_json().encode()).decode()
        with self.assertRaises(ValueError):
            await history.arrivals(identity, public_only=True, limit=20, cursor=cursor)
        with self.assertRaises(ValueError):
            await history.arrivals(identity, public_only=True, limit=101, cursor=None)
        control.run.assert_not_awaited()
        async with history.slot:
            with self.assertRaises(HistoryUnavailable):
                await history.arrivals(identity, public_only=True, limit=20, cursor=None)

    async def test_readiness_is_bounded_before_io_and_shares_history_capacity(self):
        control = AsyncMock()
        history = CollectionHistory(control)
        self.assertEqual(await history.readiness([], public_only=True), {})
        with self.assertRaises(ValueError):
            await history.readiness([uuid4() for _ in range(101)], public_only=True)
        control.run.assert_not_awaited()
        async with history.slot:
            with self.assertRaises(HistoryUnavailable):
                await history.readiness([uuid4()], public_only=True)
        control.run.assert_not_awaited()

    async def test_lineage_cursor_is_bound_to_observation_and_visibility_before_io(self):
        import base64
        from datetime import UTC, datetime
        from periplus.crawl.control.collections.lineage import LineageCursor
        control = AsyncMock()
        history = CollectionHistory(control)
        identity = uuid4()
        anchor = LineageCursor(observation_id=identity, public_only=False,
            decided_at=datetime.now(UTC), record_id=uuid4(), kind="reason")
        encode = lambda value: base64.urlsafe_b64encode(value.model_dump_json().encode()).decode()
        for cursor in ("!", "x" * 513, encode(anchor),
                       encode(anchor.model_copy(update={"observation_id": uuid4(), "public_only": True}))):
            with self.assertRaises(ValueError):
                await history.observation_lineage(identity, public_only=True, limit=20, cursor=cursor)
        with self.assertRaises(ValueError):
            await history.observation_lineage(identity, public_only=True, limit=101, cursor=None)
        control.run.assert_not_awaited()
