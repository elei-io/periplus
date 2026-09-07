import asyncio
from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from periplus_sdk import collections
from periplus_sdk.errors import ApiError, CollectionFailed, WaitTimeout
from periplus_sdk.types import CollectionSpec, CurrentCollection, HistoricalCollection


def current():
    return CurrentCollection(id=uuid4(), specification=CollectionSpec(seed_urls=("https://example.com/",)),
        status="active", priority=0, reserved_pages=0, consumed_pages=0, seeds_settled=False,
        waiting_reason=None, outcome=None, created_at=datetime.now(UTC), completed_at=None,
        admission={"estimate_unavailable_reason": "selection_not_frozen"},
        queue={"runnable_pages": 0, "deferred_pages": 0, "unknown_pages": 0,
               "oldest_admitted_at": None, "oldest_wait_seconds": None, "constraints": [],
               "basis": "stored_eligibility_permits_rechecked_at_start"},
        last_progress_at=datetime.now(UTC),
        as_of=datetime.now(UTC))


def historical(snapshot, *, outcome=None):
    return HistoricalCollection(id=snapshot.id, specification=snapshot.specification,
        created_at=snapshot.created_at, completed_at=None, outcome=outcome,
        consumed_pages=None, supplied_pages=None, failed_pages=None, seed_provenance=None,
        as_of=datetime.now(UTC))


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_preserves_intent_and_id_without_second_request(self):
        snapshot = current()
        spec = CollectionSpec(seed_sql="SELECT requested_url AS url FROM web.observation WHERE requested_url = ?",
            seed_parameters=("https://example.com/",), max_depth=2, page_limit=12, result_max_age_seconds=0)
        response = snapshot.model_copy(update={"specification": spec})
        with patch("periplus_sdk.collections.request", AsyncMock(return_value=response.model_dump(mode="json"))) as request:
            result = await collections.submit(spec, id=snapshot.id)
        self.assertEqual(result.id, snapshot.id)
        request.assert_awaited_once_with("POST", "/collections", json={
            "id": str(snapshot.id), "priority": 0, "specification": spec.model_dump(mode="json")})

    async def test_wait_transitions_to_history_and_preserves_unknown_readiness(self):
        snapshot = current()
        collection = collections.Collection(snapshot)
        with patch("periplus_sdk.collections.request", AsyncMock(side_effect=[
                historical(snapshot).model_dump(mode="json"),
                historical(snapshot, outcome="budget_reached").model_dump(mode="json")])) as request:
            result = await collection.wait(timeout=1, poll_seconds=0.001)
        self.assertIs(result, collection)
        self.assertTrue(collection.settled)
        self.assertIsNone(collection.snapshot.query_ready)
        self.assertEqual(request.await_count, 2)

    async def test_timeout_bounds_an_inflight_read_without_remote_cancellation(self):
        collection = collections.Collection(current())
        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()
        with patch("periplus_sdk.collections.request", AsyncMock(side_effect=stalled)) as request:
            with self.assertRaises(WaitTimeout):
                await collection.wait(timeout=0.02, poll_seconds=0.001)
        self.assertTrue(all(call.args[0] == "GET" for call in request.await_args_list))
        self.assertFalse(collection.settled)

    async def test_cancellation_is_local(self):
        collection = collections.Collection(current())
        with patch("periplus_sdk.collections.request", AsyncMock()) as request:
            task = asyncio.create_task(collection.wait(poll_seconds=60))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        request.assert_not_awaited()

    async def test_transient_errors_honor_retry_after(self):
        snapshot = current()
        collection = collections.Collection(snapshot)
        complete = snapshot.model_copy(update={"status": "settled", "outcome": "eligible_links_exhausted"})
        with patch("periplus_sdk.collections.request", AsyncMock(side_effect=[
                ApiError("busy", status_code=503, retry_after_seconds=2), complete.model_dump(mode="json")])), \
             patch("periplus_sdk.collections.asyncio.sleep", AsyncMock()) as sleep:
            await collection.wait(timeout=1, poll_seconds=0.1)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.1, 2])
        self.assertTrue(collection.settled)

    async def test_history_controls_rejected_and_partial_failures_distinct(self):
        collection = collections.Collection(historical(current(), outcome="cancelled"))
        with patch("periplus_sdk.collections.request", AsyncMock()) as request:
            with self.assertRaises(ValueError):
                await collection.resume()
        request.assert_not_awaited()
        with self.assertRaises(CollectionFailed):
            collection.raise_for_status()
        collection.snapshot = current().model_copy(update={"status": "settled", "outcome": "budget_reached", "failed_pages": 1})
        with self.assertRaises(CollectionFailed):
            collection.raise_for_status()

    async def test_list_and_history_send_server_pagination(self):
        snapshot = current()
        with patch("periplus_sdk.collections.request", AsyncMock(return_value={
                "source": "current", "items": [snapshot.model_dump(mode="json")], "limit": 7, "offset": 14})) as request:
            page = await collections.list(status="paused", limit=7, offset=14)
        request.assert_awaited_once_with("GET", "/collections", params={"status": "paused", "limit": 7, "offset": 14})
        self.assertEqual(page.items[0].id, snapshot.id)
        with patch("periplus_sdk.collections.request", AsyncMock(return_value={
                "source": "history", "items": [], "next_cursor": None, "as_of": datetime.now(UTC).isoformat()})) as request:
            await collections.history(limit=3, cursor="opaque")
        request.assert_awaited_once_with("GET", "/collections/history", params={"limit": 3, "cursor": "opaque"})

    async def test_mutation_conflicts_are_not_retried(self):
        snapshot = current()
        collection = collections.Collection(snapshot)
        with patch("periplus_sdk.collections.request", AsyncMock(side_effect=ApiError("conflict", status_code=409))) as request:
            with self.assertRaises(ApiError):
                await collection.set_priority(4)
        request.assert_awaited_once()
        self.assertIs(collection.snapshot, snapshot)

    async def test_item_and_arrival_pages_preserve_cursors_identity_and_unknowns(self):
        from periplus_sdk.types import CollectionItemsPage, CollectionArrivalsPage
        snapshot = current()
        collection = collections.Collection(snapshot)
        after = uuid4()
        current_page = CollectionItemsPage(collection_id=collection.id, items=[], next_after=after, as_of=datetime.now(UTC))
        arrivals = CollectionArrivalsPage(collection_id=collection.id, definition_committed=False,
            items=[], next_cursor=None, as_of=datetime.now(UTC))
        with patch('periplus_sdk.collections.request', AsyncMock(side_effect=[
                current_page.model_dump(mode='json'), arrivals.model_dump(mode='json')])) as request:
            page = await collection.items(limit=3, after=str(after))
            history_page = await collection.arrivals(limit=4, cursor='opaque-token')
        self.assertEqual(page.next_after, after)
        self.assertFalse(history_page.definition_committed)
        self.assertEqual(request.await_args_list[0].kwargs['params'], {'limit': 3, 'after': str(after)})
        self.assertEqual(request.await_args_list[1].kwargs['params'], {'limit': 4, 'cursor': 'opaque-token'})
        with patch('periplus_sdk.collections.request', AsyncMock(return_value=current_page.model_dump(mode='json'))):
            with self.assertRaisesRegex(ValueError, 'identity'):
                await collections.items(uuid4())
        with patch('periplus_sdk.collections.request', AsyncMock(return_value=arrivals.model_dump(mode='json'))):
            with self.assertRaisesRegex(ValueError, 'identity'):
                await collections.arrivals(uuid4())

    async def test_read_bounds_fail_before_network_and_storage_errors_are_not_empty_pages(self):
        snapshot = current()
        with patch('periplus_sdk.collections.request', AsyncMock()) as request:
            for call in (collections.items(snapshot.id, limit=101), collections.items(snapshot.id, after='invalid'),
                         collections.arrivals(snapshot.id, cursor='x' * 513)):
                with self.assertRaises(ValueError):
                    await call
            request.assert_not_awaited()
        with patch('periplus_sdk.collections.request', AsyncMock(side_effect=ApiError('offline', status_code=503))):
            with self.assertRaises(ApiError):
                await collections.arrivals(snapshot.id)
