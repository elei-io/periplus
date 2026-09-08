"""Cursor-based public completion reads over retained frontier state, not a history store."""
import base64
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select

from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.live import RecentCapture, _aware

_MAX_ID = UUID(int=(1 << 128) - 1)
_PAGE_SIZE = 200
# The janitor retains at least one hour of current completions. Never resume an
# unobserved interval approaching that boundary; history stays in DuckLake.
_CURSOR_TTL = timedelta(minutes=10)


class CaptureCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    after_time: datetime
    after_id: UUID
    through_time: datetime | None = None
    expires_at: datetime


def decode_capture_cursor(value: str | None) -> CaptureCursor | None:
    if value is None:
        return None
    try:
        if len(value) > 1024:
            raise ValueError("cursor too long")
        cursor = CaptureCursor.model_validate_json(base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        dates = [cursor.after_time, cursor.expires_at] + ([cursor.through_time] if cursor.through_time else [])
        if any(value.utcoffset() is None for value in dates):
            raise ValueError("cursor requires timezone-aware timestamps")
        if cursor.through_time is not None and cursor.after_time > cursor.through_time:
            raise ValueError("cursor window is reversed")
        return cursor
    except Exception as exc:
        raise ValueError("invalid capture cursor") from exc


class CapturePage(BaseModel):
    items: list[RecentCapture]
    cursor: str
    has_more: bool
    bootstrap: bool
    reset_reason: Literal["cursor_expired", "clock_moved_backwards"] | None = None
    as_of: datetime
    source: Literal["retained_public_completions"] = "retained_public_completions"


def capture_page(sessions, cursor: CaptureCursor | None = None) -> CapturePage:
    with sessions() as session:
        # Completion transitions use this same lock and assign completion time
        # after acquiring it. The window is fixed before releasing the read lock.
        session.scalar(select(FrontierControlRecord).where(FrontierControlRecord.id == 1).with_for_update(read=True))
        now = FrontierStore._transaction_now(session)
        reset_reason = None
        if cursor is not None and cursor.expires_at < now:
            reset_reason, cursor = "cursor_expired", None
        elif cursor is not None and (cursor.through_time or cursor.after_time) > now:
            reset_reason, cursor = "clock_moved_backwards", None
        upper = cursor.through_time if cursor and cursor.through_time else now
        statement = select(AcquisitionRecord.id, AcquisitionRecord.url, AcquisitionRecord.completed_at,
            AcquisitionRecord.evidence_snapshot).where(AcquisitionRecord.status == "succeeded", AcquisitionRecord.completed_at <= upper)
        if cursor is None:
            rows = list(reversed(session.execute(statement.order_by(
                AcquisitionRecord.completed_at.desc(), AcquisitionRecord.id.desc()).limit(7)).all()))
            has_more = False
        else:
            statement = statement.where(or_(AcquisitionRecord.completed_at > cursor.after_time,
                and_(AcquisitionRecord.completed_at == cursor.after_time, AcquisitionRecord.id > cursor.after_id)))
            rows = session.execute(statement.order_by(AcquisitionRecord.completed_at,
                AcquisitionRecord.id).limit(_PAGE_SIZE + 1)).all()
            has_more = len(rows) > _PAGE_SIZE
            rows = rows[:_PAGE_SIZE]
        # A completed window advances even when empty; idle heartbeats therefore
        # preserve continuity without pinning old records in operational storage.
        following = CaptureCursor(after_time=_aware(rows[-1][2]) if has_more else upper,
            after_id=rows[-1][0] if has_more else _MAX_ID,
            through_time=upper if has_more else None, expires_at=upper + _CURSOR_TTL)
        encoded = base64.urlsafe_b64encode(following.model_dump_json().encode()).decode().rstrip("=")
        return CapturePage(items=[RecentCapture(observation_id=row[0], requested_url=row[1],
            completed_at=_aware(row[2]), evidence_committed=row[3] is not None) for row in rows],
            cursor=encoded, has_more=has_more, bootstrap=cursor is None, reset_reason=reset_reason, as_of=now)
