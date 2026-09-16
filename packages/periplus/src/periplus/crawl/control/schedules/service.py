"""Short control transactions; schedules create ordinary requests and no history ledger."""
from datetime import timedelta
from uuid import UUID, uuid4, uuid5
from sqlalchemy import select

from periplus.crawl.control.collections.schemas import CollectionSpec, RequestOrigin
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.operations.access.service import AccessStore
from periplus.crawl.control.schedules.models import RequestDefinitionRecord, ScheduleRecord
from periplus.crawl.control.schedules.schemas import DefinitionInput, DefinitionView, ScheduleInput, ScheduleView, next_tick
from periplus.crawl.runtime.frontier_store import FrontierStore


class VersionConflict(ValueError):
    pass


class ScheduleStore:
    def __init__(self, sessions):
        self.sessions = sessions
        self.frontier = FrontierStore(sessions)

    def definitions(self):
        with self.sessions() as session:
            return [DefinitionView.model_validate(row) for row in session.scalars(
                select(RequestDefinitionRecord).order_by(RequestDefinitionRecord.created_at.desc()).limit(200))]

    def save_definition(self, value: DefinitionInput, identity=None, version=None):
        with self.sessions() as session, session.begin():
            self.frontier._control(session)
            row = session.get(RequestDefinitionRecord, identity) if identity else RequestDefinitionRecord(id=uuid4(), version=0)
            if row is None:
                raise KeyError(identity)
            if identity and row.version != version:
                raise VersionConflict("Definition changed; reload before saving")
            row.name, row.specification, row.priority = value.name.strip(), value.specification.model_dump(mode="json"), value.priority
            row.version += 1
            session.add(row)
            session.flush()
            return DefinitionView.model_validate(row)

    def schedules(self):
        with self.sessions() as session:
            return [ScheduleView.model_validate(row) for row in session.scalars(
                select(ScheduleRecord).order_by(ScheduleRecord.created_at.desc()).limit(200))]

    def save_schedule(self, definition_id, value: ScheduleInput, identity=None, version=None):
        with self.sessions() as session, session.begin():
            self.frontier._control(session)
            now = self.frontier._transaction_now(session, None)
            if session.get(RequestDefinitionRecord, definition_id) is None:
                raise KeyError(definition_id)
            row = session.get(ScheduleRecord, identity) if identity else ScheduleRecord(id=uuid4(), definition_id=definition_id, execution_count=0, version=0)
            if row is None or row.definition_id != definition_id:
                raise KeyError(identity)
            if identity and row.version != version:
                raise VersionConflict("Schedule changed; reload before saving")
            row.configuration, row.enabled = value.model_dump(mode="json"), value.enabled
            row.version += 1
            row.next_at = next_tick(value, now - timedelta(microseconds=1)) if value.enabled else None
            if value.max_count is not None and row.execution_count >= value.max_count:
                row.next_at = None
            session.add(row)
            session.flush()
            return ScheduleView.model_validate(row)

    def create_recurring(self, identity: UUID, specification: CollectionSpec, interval_seconds: int, *, priority: int = 0):
        """Atomically accept the first run and its recurrence; retries reuse both."""
        value = DefinitionInput(name=(specification.seed_description or (specification.seed_urls or ("Scheduled collection",))[0])[:200],
                                specification=specification, priority=priority)
        definition_id = uuid5(identity, "definition")
        schedule_id = uuid5(identity, "schedule")
        origin = RequestOrigin(definition_id=definition_id, definition_version=1, schedule_id=schedule_id)
        spec = specification.model_copy(update={"origin": origin})
        with self.sessions() as session, session.begin():
            control = self.frontier._control(session)
            schedule = session.get(ScheduleRecord, schedule_id)
            existing = session.get(CollectionRecord, identity)
            if schedule is not None:
                if schedule.configuration["interval_seconds"] != interval_seconds:
                    raise ValueError("collection identity reused with a different update frequency")
                if existing is None:
                    raise ValueError("This recurring request was already accepted and its first run has retired.")
                return self.frontier.create_collection_in_session(session, control, identity, spec, priority=priority)
            if existing is not None:
                raise ValueError("collection identity reused with different intent")
            now = self.frontier._transaction_now(session, None)
            cadence = ScheduleInput(kind="interval", interval_seconds=interval_seconds, start_at=now)
            if spec.request_class == "public":
                AccessStore(self.sessions).admit_in_session(session, "crawl", specification=spec, now=now)
            definition = RequestDefinitionRecord(id=definition_id, name=value.name, specification=specification.model_dump(mode="json"),
                                                 priority=priority, version=1)
            session.add(definition)
            session.flush()
            request = self.frontier.create_collection_in_session(session, control, identity, spec, priority=priority)
            session.add(ScheduleRecord(id=schedule_id, definition_id=definition_id, configuration=cadence.model_dump(mode="json"),
                enabled=True, version=1, execution_count=1, next_at=next_tick(cadence, now),
                last_request_id=identity, last_tick_at=now, last_result="created"))
            session.flush()
            return request

    def _launch(self, session, control, definition, schedule=None):
        origin = RequestOrigin(definition_id=definition.id, definition_version=definition.version,
                               schedule_id=schedule.id if schedule else None)
        spec = CollectionSpec.model_validate(definition.specification).model_copy(update={"origin": origin})
        if spec.request_class == "public":
            AccessStore(self.sessions).admit_in_session(session, "crawl", specification=spec)
        return self.frontier.create_collection_in_session(session, control, uuid4(), spec, priority=definition.priority)

    def run_now(self, definition_id):
        with self.sessions() as session, session.begin():
            control = self.frontier._control(session)
            definition = session.get(RequestDefinitionRecord, definition_id)
            if definition is None:
                raise KeyError(definition_id)
            return self._launch(session, control, definition).id

