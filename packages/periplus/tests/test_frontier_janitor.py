"""Navigation scanning stays bounded and resumes beyond pinned/new objects."""
from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from periplus.ingestion.objects.store import ObjectMetadata
from periplus.operations.janitor import cleanup_navigation, _navigation_acquisition_id


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
        frontier.cleanup_acquisitions.side_effect = lambda **kwargs: stop.set()
        objects = Mock()
        objects.list_objects.return_value = iter(())
        monitor = Mock()
        with patch("periplus.operations.janitor.FrontierStore", return_value=frontier), patch(
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
