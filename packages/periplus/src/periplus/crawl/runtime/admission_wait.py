"""Current frozen selection backlog, without claiming a queue position or forecast."""
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Text, cast, func, literal, select

from periplus.crawl.runtime.frontier_models import InterestRecord
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.crawl.runtime.admission_estimates import AdmissionEstimate, estimate_admission


class AdmissionWait(BaseModel):
    pending_candidates: int = 0
    preview_urls: tuple[str, ...] = ()
    oldest_selected_at: datetime | None = None
    elapsed_seconds: float | None = None
    estimate: AdmissionEstimate | None = None
    estimate_unavailable_reason: str | None


def admission_wait(session, record, *, now, control=None, workers=None):
    if record.status == 'settled':
        return AdmissionWait(estimate_unavailable_reason='collection_settled')
    checkpoint = (SelectionCheckpoint.model_validate(record.selection_checkpoint)
                  if record.selection_checkpoint else None)
    pending = len(checkpoint.urls) - checkpoint.cursor if checkpoint else 0
    preview = list(checkpoint.urls[checkpoint.cursor:checkpoint.cursor + 5]) if checkpoint else []
    dates = [checkpoint.selected_at] if pending and checkpoint.selected_at else []
    missing_time = bool(pending and not dates)
    column = InterestRecord.selection_checkpoint
    length = (func.jsonb_array_length if session.bind.dialect.name == 'postgresql'
              else func.json_array_length)
    remaining = length(column['urls']) - column['cursor'].as_integer()
    selected = column['selected_at'].as_string()
    predicates = (InterestRecord.collection_id == record.id,
                  InterestRecord.status == 'selecting', remaining > 0)
    total, earliest, dated, checkpoints = session.execute(select(
        func.coalesce(func.sum(remaining), 0), func.min(selected), func.count(selected), func.count(),
    ).where(*predicates)).one()
    pending += total
    missing_time |= dated != checkpoints
    if earliest:
        dates.append(datetime.fromisoformat(earliest))
    if len(preview) < 5:
        # Extract only the next candidate from each selected parent, never its full
        # navigation or checkpoint payload. This is a preview, not scheduling order.
        candidate = (column['urls'][column['cursor'].as_integer()].as_string()
                     if session.bind.dialect.name == 'postgresql' else
                     func.json_extract(column, literal('$.urls[') +
                         cast(column['cursor'].as_integer(), Text) + literal(']')))
        preview.extend(session.scalars(select(candidate)
            .where(*predicates).order_by(InterestRecord.created_at, InterestRecord.id)
            .limit(5 - len(preview))))
    dates = [value.replace(tzinfo=UTC) if value.tzinfo is None else value for value in dates]
    oldest = min(dates) if dates and not missing_time else None
    reason = ('collection_paused' if record.status == 'paused' else
              record.waiting_reason if pending and record.waiting_reason else
              'comparable_admission_waits_not_recorded' if pending else
              'selection_not_frozen' if not record.seeds_settled else
              'no_frozen_candidates_waiting')
    estimate = None
    if control is not None:
        estimate, forecast_reason = estimate_admission(session, record, control, workers=workers, now=now)
        if estimate is not None or reason in ('selection_not_frozen', 'comparable_admission_waits_not_recorded'):
            reason = forecast_reason
    return AdmissionWait(pending_candidates=pending, preview_urls=tuple(preview), estimate=estimate,
        oldest_selected_at=oldest,
        elapsed_seconds=max(0, (now - oldest).total_seconds()) if oldest else None,
        estimate_unavailable_reason=reason)
