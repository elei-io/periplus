"""Exact claims exclude competing writers and survive ambiguous failures."""
from datetime import UTC, datetime, timedelta
import unittest
from uuid import uuid4
from sqlalchemy import select, update
from operational_state_fixture import operational_state
from periplus.retention.identities import EvidenceRetired, WriteClaimUnavailable, _acquire, write_claims, retire
from periplus.retention.models import LakeWriteClaimRecord


class IdentityBatchTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)

    def test_competing_owner_rejected_and_expired_owner_replaced(self):
        keys = [('content', 'a')]
        first, second = uuid4(), uuid4()
        _acquire(keys, first, False)
        with self.assertRaises(WriteClaimUnavailable):
            _acquire(keys, second, False)
        with self.sessions.begin() as session:
            session.execute(update(LakeWriteClaimRecord).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        _acquire(keys, second, False)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(LakeWriteClaimRecord)).owner, second)

    def test_success_releases_but_unknown_failure_keeps_claim(self):
        with write_claims({'content': ['a', 'a']}):
            pass
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(LakeWriteClaimRecord)))
        with self.assertRaisesRegex(RuntimeError, 'unknown commit'):
            with write_claims({'content': ['a']}):
                raise RuntimeError('unknown commit')
        with self.assertRaises(WriteClaimUnavailable):
            with write_claims({'content': ['a']}, wait_seconds=0):
                self.fail('uncertain writer lost exclusion')

    def test_retirement_requires_claim_and_blocks_late_producer(self):
        with self.assertRaises(RuntimeError):
            retire('observation', 'a', datetime.now(UTC))
        with write_claims({'observation': ['a']}, allow_retired=True):
            retire('observation', 'a', datetime.now(UTC))
        with self.assertRaises(EvidenceRetired):
            with write_claims({'observation': ['a', 'b']}):
                self.fail('retired evidence resurrected')
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(LakeWriteClaimRecord)))

    def test_waiting_for_an_owner_does_not_consume_ingestion_failure_budget(self):
        from periplus.platform.catalogue.operations import is_retryable_catalogue_unavailability
        self.assertTrue(is_retryable_catalogue_unavailability(WriteClaimUnavailable('busy')))
        self.assertFalse(is_retryable_catalogue_unavailability(EvidenceRetired('retired')))
