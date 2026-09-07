import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from periplus.ingestion.objects.readiness import StorageReadiness, probe_storage
from periplus.ingestion.objects.store import FileObjectStore, ObjectMetadata
from periplus.operations.janitor import cleanup_probes


class StorageReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_disk_roundtrip_and_cached_success_leave_no_probe_objects(self):
        with TemporaryDirectory() as directory:
            store = FileObjectStore(Path(directory))
            readiness = StorageReadiness(store)
            with patch.object(store, 'put_if_absent', wraps=store.put_if_absent) as put:
                await readiness.check()
                await readiness.check()
                self.assertEqual(put.call_count, 1)
            self.assertEqual(list(store.list_objects('runtime/probes/')), [])
            await readiness.close()

    async def test_cancelled_callers_share_the_same_still_running_probe(self):
        readiness = StorageReadiness(MagicMock())
        started, release = asyncio.Event(), asyncio.Event()
        async def probe():
            started.set()
            await release.wait()
            return __import__('time').monotonic()
        with patch.object(readiness, '_probe', side_effect=probe) as run:
            caller = asyncio.create_task(readiness.check())
            await started.wait()
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            other = asyncio.create_task(readiness.check())
            await asyncio.sleep(0)
            self.assertEqual(run.call_count, 1)
            release.set()
            await other
        await readiness.close()

    async def test_timeout_keeps_the_probe_owned_until_it_finishes(self):
        readiness = StorageReadiness(MagicMock())
        release = asyncio.Event()
        async def probe():
            await release.wait()
            return __import__('time').monotonic()
        wait_for = asyncio.wait_for
        async def short_wait(task, timeout):
            return await wait_for(task, timeout=0.001)
        with patch.object(readiness, '_probe', side_effect=probe) as run:
            with patch('periplus.ingestion.objects.readiness.asyncio.wait_for',
                       side_effect=short_wait):
                for _ in range(2):
                    with self.assertRaises(TimeoutError):
                        await readiness.check()
            self.assertEqual(run.call_count, 1)
            self.assertFalse(readiness._pending.done())
            release.set()
            await readiness.check()
            self.assertEqual(run.call_count, 1)
        await readiness.close()

    async def test_failed_probe_is_not_cached_and_can_recover(self):
        readiness = StorageReadiness(MagicMock())
        with patch('periplus.ingestion.objects.readiness.probe_storage', side_effect=[OSError('offline'), None]) as probe:
            with self.assertRaises(OSError):
                await readiness.check()
            await readiness.check()
            self.assertEqual(probe.call_count, 2)
        await readiness.close()

    def test_mismatched_read_is_failure_and_attempts_cleanup(self):
        store = MagicMock()
        store.open.return_value.__enter__.return_value.read.return_value = b'wrong'
        with self.assertRaisesRegex(OSError, 'content mismatch'):
            probe_storage(store)
        store.delete.assert_called_once()

    def test_janitor_keeps_recent_probes_and_only_deletes_old_probe_keys(self):
        store = MagicMock()
        now = datetime.now(UTC)
        store.list_objects.return_value = iter([
            ObjectMetadata('runtime/probes/old.probe', 16, now - timedelta(hours=3)),
            ObjectMetadata('runtime/probes/recent.probe', 16, now),
        ])
        self.assertIsNone(cleanup_probes(store))
        store.list_objects.assert_called_once_with('runtime/probes/')
        store.delete_many.assert_called_once_with(('runtime/probes/old.probe',))
