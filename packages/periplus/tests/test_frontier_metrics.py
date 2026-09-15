from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from sqlalchemy import delete, select
from schema_fixture import create_engine
from uuid import uuid4
from sqlalchemy.orm import sessionmaker
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord, InterestRecord
from periplus.operations.api.metrics import prometheus_metrics


class FrontierMetricsTests(unittest.TestCase):
    def test_pending_includes_retries_but_not_active_and_empty_resets_age(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        for model in (FrontierControlRecord, AcquisitionRecord, InterestRecord):
            model.__table__.create(engine)
        sessions = sessionmaker(engine)
        with sessions.begin() as session:
            session.add(FrontierControlRecord(id=1, pending_count=2, active_count=1))
            for index, status in enumerate(('queued', 'retry', 'dispatched')):
                session.add(AcquisitionRecord(url=f'https://example.com/{index}', domain='example.com',
                    capture_key=str(index), requirements={}, status=status,
                    created_at=datetime.now(UTC) - timedelta(seconds=20 + index)))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(frontier_sessions=sessions)))
        body = prometheus_metrics(request).body.decode()
        self.assertIn('periplus_frontier_pending_acquisitions 2.0', body)
        self.assertIn('periplus_frontier_orphaned_pending_acquisitions 2.0', body)
        self.assertIn('periplus_frontier_owned_pending_acquisitions 0.0', body)
        with sessions.begin() as session:
            queued = session.scalar(select(AcquisitionRecord).where(AcquisitionRecord.status == 'queued'))
            session.add(InterestRecord(collection_id=uuid4(), acquisition_id=queued.id,
                url_key='test', url=queued.url, context={}, status='queued', mode='new'))
        body = prometheus_metrics(request).body.decode()
        self.assertIn('periplus_frontier_orphaned_pending_acquisitions 1.0', body)
        self.assertIn('periplus_frontier_owned_pending_acquisitions 1.0', body)
        age = float(next(line.split()[-1] for line in body.splitlines()
                         if line.startswith('periplus_frontier_oldest_pending_seconds ')))
        self.assertGreaterEqual(age, 21)
        with sessions.begin() as session:
            session.execute(delete(InterestRecord))
            session.execute(delete(AcquisitionRecord))
            session.get(FrontierControlRecord, 1).pending_count = 0
        body = prometheus_metrics(request).body.decode()
        self.assertIn('periplus_frontier_oldest_pending_seconds 0.0', body)
