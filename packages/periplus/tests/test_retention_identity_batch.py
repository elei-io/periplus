"""Exact claims exclude competing writers and survive ambiguous failures."""
from datetime import UTC, datetime, timedelta
import unittest
from uuid import uuid4
from sqlalchemy import select, update
from operational_state_fixture import operational_state
from periplus.retention.identities import WriteClaimUnavailable, _acquire, write_claims
from periplus.retention.models import WriteClaimRecord


class IdentityBatchTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)

    def test_competing_owner_rejected_and_expired_owner_replaced(self):
        keys = [('content', 'a')]
        first, second = uuid4(), uuid4()
        _acquire(keys, first)
        with self.assertRaises(WriteClaimUnavailable):
            _acquire(keys, second)
        with self.sessions.begin() as session:
            session.execute(update(WriteClaimRecord).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        _acquire(keys, second)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(WriteClaimRecord)).owner, second)

    def test_success_releases_but_unknown_failure_keeps_claim(self):
        with write_claims({'content': ['a', 'a']}):
            pass
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(WriteClaimRecord)))
        with self.assertRaisesRegex(RuntimeError, 'unknown commit'):
            with write_claims({'content': ['a']}):
                raise RuntimeError('unknown commit')
        with self.assertRaises(WriteClaimUnavailable):
            with write_claims({'content': ['a']}, wait_seconds=0):
                self.fail('uncertain writer lost exclusion')

    def test_waiting_for_an_owner_does_not_consume_ingestion_failure_budget(self):
        from periplus.materialization.rebuilds.runtime import retryable
        self.assertTrue(retryable(WriteClaimUnavailable('busy')))
        self.assertFalse(retryable(ValueError('invalid source')))

    def test_conflict_reports_exact_expiry_and_rolls_back_free_claims(self):
        owner = uuid4()
        _acquire([('content', 'busy')], owner)
        with self.assertRaises(WriteClaimUnavailable) as caught:
            with write_claims({'content': ['busy', 'free']}, wait_seconds=0):
                self.fail('blocked write entered')
        self.assertEqual(set(caught.exception.blocked_until), {('content', 'busy')})
        self.assertGreater(caught.exception.blocked_until[('content', 'busy')], datetime.now(UTC))
        with self.sessions() as session:
            rows = list(session.scalars(select(WriteClaimRecord)))
            self.assertEqual([(row.identity, row.owner) for row in rows], [('busy', owner)])
        with write_claims({'content': ['free']}, wait_seconds=0):
            pass
