from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from periplus.crawl.control.collections.schemas import CollectionSpec
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.schedules.models import RequestDefinitionRecord, ScheduleRecord
from periplus.crawl.control.schedules.schemas import DefinitionInput, ScheduleInput, next_tick
from periplus.crawl.control.schedules.service import ScheduleStore, VersionConflict
from periplus.crawl.runtime.request_schedules import create_due_requests
from periplus.crawl.runtime.frontier_models import FrontierControlRecord, FrontierOutboxRecord, AcquisitionRecord

class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        for model in (RequestDefinitionRecord, ScheduleRecord, CollectionRecord, FrontierControlRecord, AcquisitionRecord, FrontierOutboxRecord):
            model.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
        self.store = ScheduleStore(self.sessions)
        self.now = datetime(2026, 9, 8, tzinfo=UTC)
        self.definition = self.store.save_definition(DefinitionInput(name='Expansion', specification=CollectionSpec(seed_urls=('https://example.com/',))))

    def tearDown(self):
        self.engine.dispose()

    def schedule(self, **kwargs):
        spec = ScheduleInput(kind='interval', interval_seconds=60, start_at=self.now, **kwargs)
        with patch.object(self.store.frontier, '_transaction_now', return_value=self.now):
            return self.store.save_schedule(self.definition.id, spec)

    def test_atomic_creation_origin_overlap_and_max_count(self):
        schedule = self.schedule(max_count=2)
        first, = create_due_requests(self.store, self.now)
        self.assertEqual(create_due_requests(self.store, self.now), [])
        self.assertEqual(create_due_requests(self.store, self.now + timedelta(minutes=1)), [])
        with self.sessions.begin() as session:
            row = session.get(CollectionRecord, first)
            self.assertEqual(row.spec['origin']['schedule_id'], str(schedule.id))
            self.assertEqual(row.spec['origin']['definition_version'], 1)
            row.status = 'settled'
            self.assertEqual(len(list(session.scalars(select(FrontierOutboxRecord)))), 1)
        second, = create_due_requests(self.store, self.now + timedelta(minutes=2))
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.schedules()[0].execution_count, 2)
        self.assertIsNone(self.store.schedules()[0].next_at)

    def test_missed_ticks_stop_and_paused_do_not_count(self):
        self.schedule(stop_at=self.now + timedelta(minutes=10))
        self.assertEqual(create_due_requests(self.store, self.now + timedelta(minutes=5, seconds=10)), [])
        self.assertEqual(self.store.schedules()[0].last_result, 'missed_tick')
        with self.sessions.begin() as session:
            session.get(FrontierControlRecord, 1).paused = True
        self.assertEqual(create_due_requests(self.store, self.now + timedelta(minutes=6)), [])
        self.assertEqual(self.store.schedules()[0].execution_count, 0)
        self.assertEqual(create_due_requests(self.store, self.now + timedelta(minutes=10)), [])
        self.assertIsNone(self.store.schedules()[0].next_at)

    def test_edit_intent_only_changes_future_runs(self):
        first = self.store.run_now(self.definition.id)
        value = DefinitionInput(name='Next', specification=CollectionSpec(seed_urls=('https://other.example/',), request_class='system'))
        self.store.save_definition(value, self.definition.id, 1)
        second = self.store.run_now(self.definition.id)
        with self.sessions() as session:
            self.assertEqual(session.get(CollectionRecord, first).spec['seed_urls'], ['https://example.com/'])
            self.assertEqual(session.get(CollectionRecord, second).spec['seed_urls'], ['https://other.example/'])
            self.assertEqual(session.get(CollectionRecord, first).spec['request_class'], 'admin')
            self.assertEqual(session.get(CollectionRecord, second).spec['request_class'], 'system')
        with self.assertRaises(VersionConflict):
            self.store.save_definition(value, self.definition.id, 1)

    def test_cron_timezone_and_exclusive_stop(self):
        spec = ScheduleInput(kind='cron', cron='0 9 * * *', timezone='Asia/Tokyo', start_at=self.now, stop_at=self.now + timedelta(days=1))
        self.assertEqual(next_tick(spec, self.now - timedelta(microseconds=1)), self.now)
        self.assertIsNone(next_tick(spec, self.now))
        with self.assertRaises(ValueError):
            ScheduleInput(kind='cron', cron='* * * * * *', start_at=self.now)
        with self.assertRaises(ValueError):
            ScheduleInput(kind='interval', interval_seconds=60, cron='* * * * *', start_at=self.now)

    def test_failed_commit_rolls_back_run_and_schedule(self):
        self.schedule()
        with patch.object(self.store.frontier, '_lineage', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                create_due_requests(self.store, self.now)
        self.assertEqual(self.store.schedules()[0].execution_count, 0)
        with self.sessions() as session:
            self.assertEqual(list(session.scalars(select(CollectionRecord))), [])

    def test_cron_dst_skips_nonexistent_time_and_repeats_fold(self):
        spec = ScheduleInput(kind='cron', cron='30 2 * * *', timezone='America/New_York', start_at=datetime(2026,3,8,6,tzinfo=UTC))
        self.assertEqual(next_tick(spec, spec.start_at), datetime(2026,3,9,6,30,tzinfo=UTC))
        spec = ScheduleInput(kind='cron', cron='30 1 * * *', timezone='America/New_York', start_at=datetime(2026,11,1,4,tzinfo=UTC))
        first = next_tick(spec, spec.start_at)
        self.assertEqual(first, datetime(2026,11,1,5,30,tzinfo=UTC))
        self.assertEqual(next_tick(spec, first), datetime(2026,11,1,6,30,tzinfo=UTC))

    def test_each_execution_gets_fresh_frozen_duration_and_retry_preserves_it(self):
        self.store.save_definition(DefinitionInput(name='Timed', specification=CollectionSpec(
            seed_urls=('https://example.com/',), max_duration_seconds=60)), self.definition.id, 1)
        first = self.store.run_now(self.definition.id)
        second = self.store.run_now(self.definition.id)
        with self.sessions() as session:
            a, b = session.get(CollectionRecord, first), session.get(CollectionRecord, second)
            from periplus.crawl.control.schedules.schemas import aware
            self.assertEqual(a.deadline_at, aware(a.created_at) + timedelta(seconds=60))
            self.assertEqual(b.deadline_at, aware(b.created_at) + timedelta(seconds=60))
            self.assertGreater(b.deadline_at, a.deadline_at)
            payload = dict(a.spec)
            payload.pop('deadline_at')
            original = a.deadline_at
        replay = self.store.frontier.create_collection(first, CollectionSpec.model_validate(payload))
        self.assertEqual(replay.deadline_at, original)
        for duration in (0, -1, 31536001):
            with self.assertRaises(ValueError):
                CollectionSpec(max_duration_seconds=duration)
        with self.assertRaises(ValueError):
            CollectionSpec(deadline_at=self.now)


class ScheduleApiTests(unittest.TestCase):
    def test_admin_boundary_validation_and_versioned_edits(self):
        import os
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from periplus.crawl.api.schedules import router
        from periplus.platform.api_access import ApiAccessMiddleware
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        for model in (RequestDefinitionRecord, ScheduleRecord, FrontierControlRecord):
            model.__table__.create(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions.begin() as session:
            session.add(FrontierControlRecord(id=1))
        app = FastAPI()
        app.state.frontier_sessions = sessions
        app.include_router(router)
        app.add_middleware(ApiAccessMiddleware)
        with patch.dict(os.environ, {'PERIPLUS_ADMIN_API_TOKEN':'admin', 'PERIPLUS_PUBLIC_API_TOKEN':'public'}), TestClient(app) as client:
            payload = {'name':'Example', 'specification':{'seed_urls':['https://example.com/']}}
            admin = {'Authorization':'Bearer admin'}
            self.assertEqual(client.get('/request-definitions').status_code, 401)
            self.assertEqual(client.post('/request-definitions', json=payload, headers={'Authorization':'Bearer public'}).status_code, 403)
            created = client.post('/request-definitions', json=payload, headers=admin)
            self.assertEqual(created.status_code, 201, created.text)
            path = '/request-definitions/' + created.json()['id']
            updated = client.put(path, json=payload | {'name':'Changed', 'expected_version':1}, headers=admin)
            self.assertEqual(updated.json()['version'], 2)
            self.assertEqual(client.put(path, json=payload | {'expected_version':1}, headers=admin).status_code, 409)
            schedule = {'kind':'cron', 'cron':'0 9 * * *', 'timezone':'Asia/Tokyo', 'start_at':'2026-09-08T00:00:00Z', 'max_count':2}
            result = client.post(path+'/schedules', json=schedule, headers=admin)
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(client.get('/request-definitions/schedules', headers=admin).json()[0]['id'], result.json()['id'])
            self.assertEqual(client.post(path+'/schedules', json=schedule | {'cron':'* * * * * *'}, headers=admin).status_code, 422)
