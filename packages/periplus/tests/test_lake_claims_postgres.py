"""Real Postgres exclusion; opt in against the disposable development database."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from periplus.platform.postgres import session
from periplus.platform.config.performance import POSTGRES_TRANSACTION_OPTIONS
from periplus.retention.identities import _acquire, WriteClaimUnavailable, write_claims, retire, EvidenceRetired
from periplus.retention.models import LakeWriteClaimRecord, RetiredEvidenceRecord


@unittest.skipUnless(os.getenv('PERIPLUS_TEST_POSTGRES'), 'requires disposable Postgres')
class PostgresClaimTests(unittest.TestCase):
    def test_first_creation_race_has_one_owner_and_retirement_blocks_replay(self):
        schema = 'test_claims_' + uuid4().hex
        base = session.get_engine()
        with base.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        engine = create_engine(session.get_database_url(), connect_args={
            'options': POSTGRES_TRANSACTION_OPTIONS + f' -c search_path={schema}'})
        try:
            LakeWriteClaimRecord.__table__.create(engine)
            RetiredEvidenceRecord.__table__.create(engine)
            barrier = Barrier(2)
            def acquire():
                barrier.wait(timeout=5)
                try:
                    _acquire([('observation', 'a')], uuid4(), False)
                    return True
                except WriteClaimUnavailable:
                    return False
            with patch.object(session, 'SessionLocal', sessionmaker(bind=engine)), ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(acquire) for _ in range(2)]
                self.assertEqual(sorted(f.result(timeout=10) for f in futures), [False, True])
                with engine.begin() as connection:
                    connection.execute(text("UPDATE lake_write_claims SET expires_at = now() - interval '1 second'"))
                    self.assertEqual(connection.scalar(text('SHOW transaction_timeout')), '4min')
                with write_claims({'observation': ['a']}, allow_retired=True):
                    retire('observation', 'a', datetime.now(UTC))
                with self.assertRaises(EvidenceRetired):
                    with write_claims({'observation': ['a']}):
                        self.fail('retired writer entered')
        finally:
            engine.dispose()
            with base.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
