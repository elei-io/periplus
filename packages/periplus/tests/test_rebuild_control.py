"""Operator transitions and stale-worker/publication fences."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from periplus.materialization.rebuilds.models import BuildRecord, RangeRecord, PublicationRecord
from periplus.materialization.rebuilds.control import BuildControl, RebuildConflict, BOOTSTRAP_ID


class RebuildControlTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        for model in (BuildRecord, RangeRecord, PublicationRecord):
            model.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.control = BuildControl(self.sessions)
        self.control.bootstrap()

    def test_resume_fences_previous_owner_and_cancelled_page(self):
        identity = self.control.create(32)
        with self.assertRaises(RebuildConflict): self.control.create(32)
        self.control.plan(identity, 0, [(202609, ['z', '2026-09-15', str(identity)])])
        self.assertTrue(self.control.checkpoint(identity, 0, 202609, ['a', '2026-09-15', str(identity)], 3, False))
        self.control.fence_workers()
        self.assertFalse(self.control.checkpoint(identity, 0, 202609, ['z'], 90, True))
        current = self.control.get(identity)
        self.control.action(identity, 'cancel')
        self.assertFalse(self.control.checkpoint(identity, current.revision, 202609, ['z'], 90, True))
        self.assertEqual(self.control.ranges(identity)[0].processed, 3)
        self.assertTrue(self.control.get(identity).protected)
        self.assertEqual(self.control.binding()['database'], 'public_v1')

    def test_activation_requires_completed_fresh_candidate(self):
        identity = self.control.create(32)
        with self.assertRaises(RebuildConflict): self.control.action(identity, 'activate')
        self.control.change(identity, 0, phase='ready', barrier=10, ingestion_floor=10, material_floor=9)
        with self.assertRaises(RebuildConflict): self.control.action(identity, 'activate')
        self.control.change(identity, 0, material_floor=10, blocker='missing raw')
        with self.assertRaises(RebuildConflict): self.control.action(identity, 'activate')
        self.control.action(identity, 'retry')
        with self.assertRaises(RebuildConflict): self.control.action(identity, 'activate')
        current = self.control.get(identity)
        self.control.change(identity, current.revision, phase='ready')
        self.control.action(identity, 'activate')
        self.assertEqual(self.control.binding()['database'], 'query_' + identity.hex)
        self.assertEqual(self.control.get(BOOTSTRAP_ID).phase, 'previous')
        self.assertFalse(self.control.change(identity, 0, phase='ready'))
