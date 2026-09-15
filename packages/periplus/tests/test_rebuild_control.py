"""Parallel ranges, live progress, exact ownership and publication fences."""
from datetime import UTC, datetime, timedelta
import unittest
from schema_fixture import create_engine
from sqlalchemy.orm import sessionmaker
from periplus.materialization.rebuilds.models import BuildRecord, RangeRecord, BatchRecord, PublicationRecord
from periplus.materialization.rebuilds.control import BuildControl, RebuildConflict, BOOTSTRAP_ID


class RebuildControlTests(unittest.TestCase):
    def setUp(self):
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        for model in (BuildRecord,RangeRecord,BatchRecord,PublicationRecord):model.__table__.create(engine)
        self.sessions=sessionmaker(engine,expire_on_commit=False)
        self.control=BuildControl(self.sessions)
        self.control.bootstrap()
        self.control.initialize(self.control.get(BOOTSTRAP_ID),'manifest',[0]*16)

    def claim(self, identity, worker):
        return self.control.claim(identity, worker, recipe=self.control.get(BOOTSTRAP_ID).recipe)

    def test_server_cancelled_query_retries_without_releasing_uncertain_ownership(self):
        from uuid import uuid4
        from periplus.materialization.rebuilds.runtime import retryable
        from periplus.materialization.storage import MaterialInputError
        from periplus.platform.clickhouse import ClickHouseError

        cancelled = ClickHouseError("restart-proof", code="394")
        wrapped = MaterialInputError(uuid4(), cancelled)
        wrapped.__cause__ = cancelled
        build = self.candidate([1] + [0] * 15)
        planned = self.control.plan(build, [1] + [0] * 15)[0]
        claimed, _ = self.claim(planned.id, "before-restart")
        self.control.fail(claimed, str(wrapped), retryable(wrapped))
        queued = self.control.batches(build.id)[0]
        self.assertEqual(queued.status, "queued")
        self.assertEqual(queued.owner, claimed.owner)
        self.assertIsNotNone(queued.lease_until)
        self.assertIsNone(self.claim(planned.id, "after-restart"))
        self.assertFalse(retryable(ValueError("Material output exceeds row byte budget")))

    def test_restore_setup_can_retry_its_exact_manifest(self):
        self.control.bootstrap('manifest')
        self.assertEqual(len(self.control.builds()), 1)
        with self.assertRaises(RebuildConflict):
            self.control.bootstrap('different-manifest')

    def test_wrong_recipe_cannot_claim_or_poison_another_build(self):
        build = self.candidate([1]+[0]*15)
        batch = self.control.plan(build,[1]+[0]*15)[0]
        self.assertIsNone(self.control.claim(batch.id,'wrong-release',recipe='0'*64))
        self.assertEqual(self.control.batches(build.id)[0].attempts,0)
        self.assertIsNotNone(self.claim(batch.id,'correct-release'))

    def candidate(self,heads):
        identity=self.control.create(2)
        build=self.control.get(identity)
        self.control.initialize(build,'manifest',heads)
        return self.control.get(identity)

    def test_different_workers_process_parallel_shards_without_duplicate_claim(self):
        build=self.candidate([3,3]+[0]*14)
        batches=self.control.plan(build,[3,3]+[0]*14)
        first=self.claim(batches[0].id,'worker-a')[0]
        second=self.claim(batches[1].id,'worker-b')[0]
        self.assertNotEqual(first.shard,second.shard)
        self.assertIsNone(self.claim(first.id,'worker-b'))
        self.assertTrue(self.control.finish(first,2))
        self.assertFalse(self.control.finish(first,2))
        self.assertTrue(self.control.finish(second,2))
        self.assertEqual([r.cursor for r in self.control.ranges(build.id)][:2],[2,2])
        self.assertEqual(len(self.control.plan(build,[3,3]+[0]*14)),2)

    def test_pausing_history_keeps_live_arrivals_queryable(self):
        build=self.candidate([5]+[0]*15)
        self.control.action(build.id,'pause')
        batches=self.control.plan(self.control.get(build.id),[7]+[0]*15)
        self.assertEqual([(b.lane,b.start,b.end) for b in batches],[('live',6,7)])
        batch,_=self.claim(batches[0].id,'live-worker')
        self.control.finish(batch,2)
        self.control.verify(build,[7]+[0]*15)
        self.assertIsNone(self.control.get(build.id).verified_at)
        self.control.action(build.id,'resume')
        self.assertEqual(self.control.plan(self.control.get(build.id),[7]+[0]*15)[0].lane,'history')

    def test_cancel_fences_worker_progress_and_protects_until_drained(self):
        build=self.candidate([2]+[0]*15)
        batch,_=self.claim(self.control.plan(build,[2]+[0]*15)[0].id,'old-worker')
        self.control.action(build.id,'cancel')
        self.assertFalse(self.control.finish(batch,2))
        self.assertIsNone(self.claim(batch.id,'new-worker'))
        cancelled=self.control.get(build.id)
        self.assertTrue(cancelled.protected)
        self.assertGreater(cancelled.drain_after,datetime.now(UTC).replace(tzinfo=None))
        self.assertEqual(self.control.binding()['database'],'public_v1')

    def test_failure_blocks_readiness_and_retry_is_explicit(self):
        build=self.candidate([1]+[0]*15)
        batch,_=self.claim(self.control.plan(build,[1]+[0]*15)[0].id,'worker')
        self.control.fail(batch,'Missing raw bytes',False)
        self.control.verify(build,[1]+[0]*15)
        with self.assertRaises(RebuildConflict):self.control.action(build.id,'activate')
        self.assertEqual(self.control.get(build.id).blocker,'Missing raw bytes')
        self.control.action(build.id,'retry')
        retried,_=self.claim(batch.id,'worker')
        self.assertTrue(self.control.finish(retried,1))
        self.control.verify(self.control.get(build.id),[1]+[0]*15)
        self.control.action(build.id,'activate')
        self.assertEqual(self.control.binding()['database'],'query_'+build.id.hex)
        self.assertEqual(self.control.get(BOOTSTRAP_ID).phase,'previous')

    def test_uncertain_write_cannot_be_reclaimed_until_old_writer_drains(self):
        build=self.candidate([1]+[0]*15)
        batch,_=self.claim(self.control.plan(build,[1]+[0]*15)[0].id,'old')
        self.control.fail(batch,'Disconnected during INSERT',True)
        self.assertIsNone(self.claim(batch.id,'replacement'))
        with self.sessions.begin() as session:session.get(BatchRecord,batch.id).lease_until=datetime.now(UTC)-timedelta(seconds=1)
        replacement,_=self.claim(batch.id,'replacement')
        self.assertNotEqual(batch.owner,replacement.owner)
        self.assertFalse(self.control.finish(batch,1))
        self.assertTrue(self.control.finish(replacement,1))
