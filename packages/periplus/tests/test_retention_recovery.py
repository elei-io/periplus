"""Busy content is deferred; storage/lease loss still prevents reclamation."""
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from test_operation_leases import FakeBucket
from periplus.platform.messaging.leases import operation_leases, OperationLeaseLost
from periplus.retention.runtime import RetentionSettings, reclaim_pass
from periplus.retention.publications import cleanup_publications


class RetentionRecoveryTests(IsolatedAsyncioTestCase):
    async def test_busy_content_does_not_block_other_keys_and_is_revisited(self):
        bucket = FakeBucket()
        now = datetime.now(UTC)
        rows = [('a', now, 'raw/a'), ('b', now, 'raw/b')]
        reclaim = Mock(return_value=1)
        with patch('periplus.retention.store.candidates', side_effect=[rows, [], rows]) as candidates, patch(
                'periplus.retention.runtime.catalogue_from_env', side_effect=lambda **kw: nullcontext(object())), patch(
                'periplus.retention.runtime.RetentionCatalogue', return_value=SimpleNamespace(reclaim_objects=reclaim)):
            settings = RetentionSettings(mode='purge', batch_size=2)
            async with operation_leases(bucket, ['content:a'], phase='ingestion'):
                removed, after = await reclaim_pass(settings, object(), bucket)
                self.assertEqual(removed, 1)
                self.assertEqual(after, (now, 'raw/b'))
                self.assertEqual(reclaim.call_args.kwargs['content_hashes'], ('b',))
                self.assertEqual(reclaim.call_args.kwargs['limit'], 1)
            self.assertEqual(await reclaim_pass(settings, object(), bucket, after), (0, None))
            self.assertEqual((await reclaim_pass(settings, object(), bucket))[0], 2)
        self.assertEqual(candidates.call_args_list[1].args, (2, (now, 'raw/b')))
        self.assertEqual([c.kwargs['content_hashes'] for c in reclaim.call_args_list], [('b',), ('a',), ('b',)])
        self.assertFalse(bucket.values)

    async def test_duplicate_content_shares_lease_without_expanding_batch_budget(self):
        now = datetime.now(UTC)
        reclaim = Mock(return_value=0)
        with patch('periplus.retention.store.candidates', return_value=[('a', now, 'one'), ('a', now, 'two')]), patch(
                'periplus.retention.runtime.catalogue_from_env', return_value=nullcontext(object())), patch(
                'periplus.retention.runtime.RetentionCatalogue', return_value=SimpleNamespace(reclaim_objects=reclaim)):
            await reclaim_pass(RetentionSettings(mode='purge', batch_size=2), object(), FakeBucket())
        self.assertEqual(reclaim.call_count, 1)
        self.assertEqual(reclaim.call_args.kwargs['limit'], 2)

    async def test_infrastructure_failure_is_not_treated_as_busy_content(self):
        class BrokenBucket(FakeBucket):
            async def create(self, key, value):
                raise PermissionError('lease write denied')
        with patch('periplus.retention.store.candidates', return_value=[('a', datetime.now(UTC), 'one')]), patch(
                'periplus.retention.runtime.catalogue_from_env') as opened:
            with self.assertRaises(PermissionError):
                await reclaim_pass(RetentionSettings(mode='purge'), object(), BrokenBucket())
            opened.assert_not_called()

    async def test_busy_publication_does_not_abort_scan(self):
        now = datetime.now(UTC)
        old = now - timedelta(days=365)
        content = 'a' * 64
        items = [SimpleNamespace(key=f'runtime/publications/{content}/{uuid4()}', last_modified=old),
                 SimpleNamespace(key=f'runtime/publications/{"b" * 64}/{uuid4()}', last_modified=old)]
        objects = SimpleNamespace(list_objects=lambda _: iter(items))
        roots = Mock(return_value=({uuid4()}, set()))  # Retain the second publication conservatively.
        bucket = FakeBucket()
        with patch('periplus.retention.publications.current_roots', roots):
            async with operation_leases(bucket, [f'content:{content}'], phase='ingestion'):
                await cleanup_publications(RetentionSettings(mode='purge'), None, objects, bucket)
        self.assertEqual(roots.call_count, 1)
        self.assertEqual(str(roots.call_args.args[1][0]), items[1].key.split('/')[-1])
        self.assertFalse(bucket.values)

    async def test_lease_loss_still_fails_reclamation(self):
        reclaim = Mock(side_effect=OperationLeaseLost('lost'))
        with patch('periplus.retention.store.candidates', return_value=[('a', datetime.now(UTC), 'one')]), patch(
                'periplus.retention.runtime.catalogue_from_env', return_value=nullcontext(object())), patch(
                'periplus.retention.runtime.RetentionCatalogue', return_value=SimpleNamespace(reclaim_objects=reclaim)):
            with self.assertRaises(OperationLeaseLost):
                await reclaim_pass(RetentionSettings(mode='purge'), object(), FakeBucket())
