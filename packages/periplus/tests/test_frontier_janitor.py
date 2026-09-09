"""Navigation scanning stays bounded and resumes beyond pinned/new objects."""
from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from periplus.ingestion.objects.store import ObjectMetadata
from periplus.operations.janitor import cleanup_navigation, cleanup_frontier, _navigation_acquisition_id
from periplus.crawl.runtime.frontier_store import CleanupBatch


class FrontierJanitorTests(unittest.TestCase):
    def test_scans_bounded_batches_without_restarting_at_pinned_objects(self):
        items = [ObjectMetadata(f"runtime/navigation/{uuid4().hex}/{'a' * 64}.arrow", 10,
                                datetime.now(UTC) - timedelta(hours=3)) for _ in range(5)]
        objects = Mock()
        objects.list_objects.return_value = iter(items)
        frontier = Mock()
        frontier.retire_navigation.side_effect = [False, False, True, True, True]
        with patch("periplus.operations.janitor.NAVIGATION_CLEANUP_BATCH_SIZE", 2):
            cursor = cleanup_navigation(frontier, objects)
            self.assertEqual(frontier.retire_navigation.call_count, 2)
            objects.delete_many.assert_not_called()
            cursor = cleanup_navigation(frontier, objects, cursor)
            cursor = cleanup_navigation(frontier, objects, cursor)
        self.assertIsNone(cursor)
        objects.list_objects.assert_called_once()
        self.assertEqual(frontier.retire_navigation.call_count, 5)
        self.assertEqual(objects.delete_many.call_args_list[0].args[0], tuple(item.key for item in items[2:4]))

    def test_failed_delete_is_not_reported_as_success_and_can_be_retried(self):
        item = ObjectMetadata(f"runtime/navigation/{uuid4().hex}/{'b' * 64}.arrow", 10,
                              datetime.now(UTC) - timedelta(hours=3))
        objects = Mock()
        objects.list_objects.side_effect = lambda prefix: iter((item,))
        objects.delete_many.side_effect = [OSError("storage unavailable"), 1]
        frontier = Mock()
        frontier.retire_navigation.return_value = True
        with self.assertRaises(OSError):
            cleanup_navigation(frontier, objects)
        self.assertIsNone(cleanup_navigation(frontier, objects))
        self.assertEqual(objects.delete_many.call_count, 2)

    def test_scan_never_treats_other_repository_prefixes_as_navigation(self):
        self.assertIsNone(_navigation_acquisition_id("raw/html/object"))
        self.assertIsNone(_navigation_acquisition_id("runtime/navigation/not-a-uuid/object.arrow"))


class FrontierJanitorLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_runs_frontier_cleanup_after_navigation_without_graph_delivery(self):
        import asyncio
        from periplus.operations.janitor import _run
        stop = asyncio.Event()
        frontier = Mock()
        frontier.cleanup_collections.return_value = CleanupBatch(0, False)
        frontier.cleanup_acquisitions.side_effect = lambda **kwargs: (stop.set(), CleanupBatch(0, False))[1]
        objects = Mock()
        objects.list_objects.return_value = iter(())
        monitor = Mock()
        with patch("periplus.retention.identities.cleanup_expired_claims"), patch("periplus.operations.janitor.FrontierStore", return_value=frontier), patch(
                "periplus.operations.janitor.object_store_from_env", return_value=objects), patch(
                "periplus.operations.query_history.store.QueryHistoryStore.cleanup", return_value=0) as history_cleanup:
            await asyncio.wait_for(_run(stop, monitor), 5)
        frontier.validate_installed.assert_called_once()
        frontier.cleanup_acquisitions.assert_called_once()
        self.assertIsNotNone(frontier.cleanup_acquisitions.call_args.kwargs["cutoff"].utcoffset())
        monitor.subsystem_ready.assert_any_call("frontier_retention")
        history_cleanup.assert_called_once()
        monitor.subsystem_ready.assert_any_call("query_history")
        monitor.subsystem_unavailable.assert_not_called()


class FrontierCleanupWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_drains_past_protected_batch_and_stops_at_scan_end(self):
        import asyncio
        frontier = Mock()
        frontier.cleanup_collections.return_value = CleanupBatch(0, False)
        frontier.cleanup_acquisitions.side_effect = [
            CleanupBatch(0, True), CleanupBatch(64, True), CleanupBatch(3, False),
        ]
        self.assertFalse(await cleanup_frontier(frontier, asyncio.Event()))
        self.assertEqual(frontier.cleanup_acquisitions.call_count, 3)
        frontier.cleanup_collections.assert_called_once()
        cutoffs = [call.kwargs['cutoff'] for call in frontier.cleanup_acquisitions.call_args_list]
        self.assertEqual(len(set(cutoffs)), 1)

    async def test_continues_partial_collection_pruning_without_removal(self):
        import asyncio
        frontier = Mock()
        frontier.cleanup_collections.side_effect = [CleanupBatch(0, True), CleanupBatch(1, False)]
        frontier.cleanup_acquisitions.side_effect = [CleanupBatch(0, False), CleanupBatch(1, False)]
        self.assertFalse(await cleanup_frontier(frontier, asyncio.Event()))
        self.assertEqual(frontier.cleanup_collections.call_count, 2)
        self.assertEqual(frontier.cleanup_acquisitions.call_count, 2)

    async def test_budget_yields_with_backlog_after_completed_transaction(self):
        import asyncio
        frontier = Mock()
        frontier.cleanup_collections.return_value = CleanupBatch(0, False)
        frontier.cleanup_acquisitions.return_value = CleanupBatch(64, True)
        with patch('periplus.operations.janitor.time') as clock:
            clock.monotonic.side_effect = [0, 0, 31]
            self.assertTrue(await cleanup_frontier(frontier, asyncio.Event()))
        frontier.cleanup_acquisitions.assert_called_once()

    async def test_stop_prevents_another_batch(self):
        import asyncio
        stop = asyncio.Event()
        frontier = Mock()
        frontier.cleanup_collections.return_value = CleanupBatch(0, False)
        frontier.cleanup_acquisitions.side_effect = lambda **kwargs: (stop.set(), CleanupBatch(64, True))[1]
        self.assertTrue(await cleanup_frontier(frontier, stop))
        frontier.cleanup_acquisitions.assert_called_once()

    async def test_cancellation_waits_for_database_batch(self):
        import asyncio
        import threading
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        frontier = Mock()
        frontier.cleanup_collections.return_value = CleanupBatch(0, False)
        def acquire(**kwargs):
            entered.set()
            release.wait(5)
            finished.set()
            return CleanupBatch(1, True)
        frontier.cleanup_acquisitions.side_effect = acquire
        task = asyncio.create_task(cleanup_frontier(frontier, asyncio.Event()))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        frontier.cleanup_acquisitions.assert_called_once()

    async def test_worker_uses_short_retry_only_while_scan_is_unfinished(self):
        import asyncio
        from unittest.mock import AsyncMock
        from periplus.operations.janitor import _run
        stop, waits = asyncio.Event(), []
        async def wait(awaitable, *, timeout):
            awaitable.close()
            waits.append(timeout)
            if len(waits) == 2:
                stop.set()
            raise TimeoutError
        objects = Mock()
        objects.list_objects.side_effect = lambda prefix: iter(())
        with patch('periplus.operations.janitor.cleanup_frontier', new=AsyncMock(side_effect=[True, False])), patch(
                'periplus.operations.janitor.FrontierStore', return_value=Mock()), patch(
                'periplus.operations.janitor.object_store_from_env', return_value=objects), patch(
                'periplus.retention.identities.cleanup_expired_claims'), patch(
                'periplus.operations.query_history.store.QueryHistoryStore.cleanup', return_value=0), patch(
                'periplus.operations.janitor.asyncio.wait_for', side_effect=wait), patch(
                'periplus.operations.janitor.bounded_call', new=AsyncMock(return_value=0)):
            await _run(stop, Mock())
        self.assertEqual(waits, [1.0, 300.0])
