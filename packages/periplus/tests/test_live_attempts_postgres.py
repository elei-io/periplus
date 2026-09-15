"""Physical retry counts come from bounded operational evidence, not ClickHouse."""
from datetime import UTC, datetime, timedelta
import json
import os
import unittest
from uuid import uuid4
from sqlalchemy import text
from periplus.platform.postgres.session import get_database_url
from postgres_fixture import isolated_database
from periplus.crawl.runtime.live import count_attempt_starts

@unittest.skipUnless(os.getenv('PERIPLUS_TEST_POSTGRES'), 'requires disposable Postgres')
class AttemptCountTests(unittest.TestCase):
    def test_retries_uncertain_starts_and_terminal_duplicates_are_counted_once(self):
        now=datetime.now(UTC)
        starts=[now-timedelta(seconds=n) for n in (10,5,1)]
        with isolated_database(get_database_url()) as engine, engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA state"))
            connection.execute(text('''CREATE TABLE state.frontier_acquisitions (
                id uuid, attempt_count integer, completed_at timestamptz,
                attempt_started_at timestamptz, prior_results jsonb,
                uncertain_attempts jsonb, outcome jsonb)'''))
            connection.execute(text('''INSERT INTO state.frontier_acquisitions VALUES
                (:id,3,:now,:last,CAST(:prior AS jsonb),CAST(:uncertain AS jsonb),CAST(:outcome AS jsonb))'''),
                {'id':uuid4(),'now':now,'last':starts[2],
                 'prior':json.dumps([{'attempt_evidence':{'started_at':starts[0].isoformat()}}]),
                 'uncertain':json.dumps([{'started_at':starts[1].isoformat()}]),
                 'outcome':json.dumps({'attempts':[{'started_at':s.isoformat()} for s in starts]})})
            self.assertEqual(count_attempt_starts(connection,since=now-timedelta(seconds=60),until=now),3)
            self.assertEqual(count_attempt_starts(connection,since=now-timedelta(seconds=3),until=now),1)
