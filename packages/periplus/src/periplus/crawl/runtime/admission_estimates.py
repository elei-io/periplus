"""Conditional first-admission ranges from retained current-request observations."""
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.runtime.start_estimates import StartEstimate, observed_range


class AdmissionTiming(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_version: int
    priority: int
    first_admitted_at: datetime | None


class AdmissionEstimate(StartEstimate):
    basis: Literal['recent_single_url_submission_to_admission_waits'] = 'recent_single_url_submission_to_admission_waits'
    scope: Literal['first_admission'] = 'first_admission'


def estimate_admission(session, record, control, *, workers, now):
    if record.status != 'active':
        return None, 'collection_' + record.status
    spec = record.spec
    if (len(spec['seed_urls']) != 1 or spec['seed_sql'] or spec['seed_description']
            or spec.get('max_duration_seconds') is not None):
        return None, 'comparable_selection_work_not_observed'
    if record.admission_timing is None:
        return None, 'request_admission_timing_not_recorded'
    timing = AdmissionTiming.model_validate(record.admission_timing)
    if timing.first_admitted_at is not None:
        return None, 'first_admission_complete'
    if timing.policy_version != control.policy_version:
        return None, 'admission_controls_changed'
    # Selection/admission continues while browser dependencies are unavailable.
    # Recent process presence is relevant here; dispatch readiness is not.
    if (workers is None or workers.state != 'observed' or not workers.reported_workers
            or not 0 <= (now - workers.as_of).total_seconds() <= 3):
        return None, 'selection_worker_presence_not_observed'
    first = CollectionRecord.admission_timing['first_admitted_at'].as_string()
    length = func.jsonb_array_length if session.bind.dialect.name == 'postgresql' else func.json_array_length
    rows = session.execute(select(CollectionRecord.created_at, first,
        CollectionRecord.spec['seed_urls'][0].as_string()).where(
        length(CollectionRecord.spec['seed_urls']) == 1,
        CollectionRecord.spec['seed_sql'].as_string().is_(None),
        CollectionRecord.spec['seed_description'].as_string().is_(None),
        CollectionRecord.spec['max_duration_seconds'].as_string().is_(None),
        CollectionRecord.spec['result_max_age_seconds'].as_integer() == spec['result_max_age_seconds'],
        CollectionRecord.admission_timing['policy_version'].as_integer() == control.policy_version,
        CollectionRecord.admission_timing['priority'].as_integer() == record.priority,
        CollectionRecord.created_at >= now - timedelta(seconds=600),
        first.is_not(None), func.length(first) <= 64,
    ).order_by(CollectionRecord.created_at.desc(), CollectionRecord.id).limit(20)).all()
    waits, newest = [], None
    for submitted, admitted, url in rows:
        admitted = datetime.fromisoformat(admitted)
        submitted = submitted.replace(tzinfo=UTC) if submitted.tzinfo is None else submitted
        if (admitted.tzinfo is None or not now - timedelta(seconds=600) <= admitted <= now
                or urlsplit(url).hostname != urlsplit(spec['seed_urls'][0]).hostname):
            continue
        delay = (admitted - submitted).total_seconds()
        if 0 <= delay <= 600:
            waits.append(delay)
            newest = max(newest, admitted) if newest else admitted
    if len(waits) < 3:
        return None, 'insufficient_comparable_admissions'
    if newest < now - timedelta(seconds=120):
        return None, 'comparable_admissions_are_stale'
    submitted = record.created_at.replace(tzinfo=UTC) if record.created_at.tzinfo is None else record.created_at
    due = record.service_after.replace(tzinfo=UTC) if record.service_after.tzinfo is None else record.service_after
    estimate = observed_range(waits, admitted_at=submitted, eligible_at=max(now, due), now=now)
    if estimate is None:
        return None, 'current_wait_outside_observed_admissions'
    return AdmissionEstimate(**estimate.model_dump(exclude={'basis'})), None
