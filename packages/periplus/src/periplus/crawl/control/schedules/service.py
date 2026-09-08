"""Short control transactions; schedules create ordinary requests and no history ledger."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select

from periplus.crawl.control.collections.schemas import CollectionSpec, RequestOrigin
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

    def _launch(self, session, control, definition, schedule=None):
        origin = RequestOrigin(definition_id=definition.id, definition_version=definition.version,
                               schedule_id=schedule.id if schedule else None)
        spec = CollectionSpec.model_validate(definition.specification).model_copy(update={"origin": origin})
        return self.frontier.create_collection_in_session(session, control, uuid4(), spec, priority=definition.priority)

    def run_now(self, definition_id):
        with self.sessions() as session, session.begin():
            control = self.frontier._control(session)
            definition = session.get(RequestDefinitionRecord, definition_id)
            if definition is None:
                raise KeyError(definition_id)
            return self._launch(session, control, definition).id

