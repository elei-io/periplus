"""Evaluate due schedules and create ordinary requests atomically."""
from sqlalchemy import select
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.schedules.models import RequestDefinitionRecord, ScheduleRecord
from periplus.crawl.control.schedules.schemas import ScheduleInput, aware, next_tick

def create_due_requests(store, now=None):
    observations = []
    with store.sessions() as session, session.begin():
        control = store.frontier._control(session)
        now = store.frontier._transaction_now(session, now)
        rows = session.scalars(select(ScheduleRecord).where(ScheduleRecord.enabled.is_(True),
            ScheduleRecord.next_at <= now).order_by(ScheduleRecord.next_at, ScheduleRecord.id).limit(20))
        created = []
        for row in rows:
            spec = ScheduleInput.model_validate(row.configuration)
            due = aware(row.next_at)
            observations.append((row, (now - due).total_seconds()))
            row.last_tick_at = due
            row.next_at = next_tick(spec, now)
            if spec.stop_at is not None and now >= spec.stop_at or spec.max_count is not None and row.execution_count >= spec.max_count:
                row.next_at, row.last_result = None, "finished"
                continue
            # Small dispatch tolerance permits normal polling jitter; downtime never replays old ticks.
            if (now - due).total_seconds() > 5:
                row.last_result = "missed_tick"
                continue
            previous = session.get(CollectionRecord, row.last_request_id) if row.last_request_id else None
            if previous is not None and previous.status != "settled":
                row.last_result = "previous_request_active"
                continue
            if control.paused:
                row.last_result = "crawler_paused"
                continue
            definition = session.get(RequestDefinitionRecord, row.definition_id)
            request = store._launch(session, control, definition, row)
            row.last_request_id = request.id
            row.execution_count += 1
            row.last_result = "created"
            if spec.max_count is not None and row.execution_count >= spec.max_count:
                row.next_at = None
            created.append(request.id)
        observations = [(row.last_result, lateness) for row, lateness in observations]
    from periplus.operations.metrics import schedule_ticks, schedule_lateness
    for outcome, lateness in observations:
        schedule_ticks.labels(outcome).inc()
        schedule_lateness.observe(max(0, lateness))
    return created
