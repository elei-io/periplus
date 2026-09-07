"""Ephemeral dispatch health; it neither owns frontier state nor promises capacity."""
import asyncio
from datetime import UTC, datetime, timedelta
import time
from typing import Literal

from nats.js.errors import NotFoundError
from pydantic import AwareDatetime, BaseModel, ConfigDict

DependencyReason = Literal['ingestion_delivery_unavailable', 'storage_unavailable', 'cdp_unavailable']


class DispatchReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    state: Literal['checking', 'ready', 'blocked', 'unknown']
    reason: DependencyReason | None = None
    checked_at: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None


class DispatchHealth:
    def __init__(self):
        self.report = DispatchReadiness(state='unknown')
        self.valid_until = 0.0

    def checking(self):
        self.report = DispatchReadiness(state='checking', checked_at=datetime.now(UTC))

    def blocked(self, reason: DependencyReason):
        self.report = DispatchReadiness(state='blocked', reason=reason, checked_at=datetime.now(UTC))

    def ready(self):
        now = datetime.now(UTC)
        self.valid_until = time.monotonic() + 5
        self.report = DispatchReadiness(state='ready', checked_at=now, valid_until=now + timedelta(seconds=5))

    def snapshot(self) -> DispatchReadiness:
        if self.report.state == 'ready' and time.monotonic() >= self.valid_until:
            return DispatchReadiness(state='unknown')
        return self.report


class CrawlerActivity(BaseModel):
    as_of: datetime
    state: Literal['observed', 'no_recent_reports', 'unavailable']
    reason: str | None = None
    reported_workers: int | None = None
    ready_workers: int | None = None
    blocked_workers: int | None = None
    checking_workers: int | None = None
    unknown_workers: int | None = None
    waiting_reasons: list[DependencyReason] = []
    excluded_reports: int | None = None
    more_workers: bool = False


class CrawlerPresenceReader:
    """Reuse one startup-owned KV handle, with one bounded read and a short cache."""
    def __init__(self, jetstream):
        self.jetstream = jetstream
        self._lock = asyncio.Lock()
        self._cached: CrawlerActivity | None = None
        self._expires = 0.0

    async def read(self) -> CrawlerActivity:
        try:
            async with asyncio.timeout(2):
                async with self._lock:
                    if self._cached is not None and time.monotonic() < self._expires:
                        return self._cached
                    value = await self._read()
                    self._cached, self._expires = value, time.monotonic() + 2
                    return value
        except Exception:
            return CrawlerActivity(as_of=datetime.now(UTC), state='unavailable', reason='worker_presence_unavailable')

    async def _read(self) -> CrawlerActivity:
        # The KV bucket itself is capped at 1 MiB. Decode at most 128 reports,
        # never infer global counts from a truncated or stale preview.
        from periplus.crawl.runtime.frontier_queue import CrawlerPresence, CRAWLER_PRESENCE_BUCKET
        now = datetime.now(UTC)
        stream = "KV_" + CRAWLER_PRESENCE_BUCKET
        info = await self.jetstream.stream_info(stream, subjects_filter=f"$KV.{CRAWLER_PRESENCE_BUCKET}.>")
        keys = list(info.state.subjects or {})
        counts = {'ready': 0, 'blocked': 0, 'checking': 0, 'unknown': 0}
        excluded, reasons = 0, set()
        for key in sorted(keys)[:128]:
            try:
                entry = await self.jetstream.get_msg(stream, subject=key)
            except NotFoundError:
                excluded += 1
                continue
            if len(entry.data) > 16384 or entry.time is None or not 0 <= (now - entry.time).total_seconds() <= 15:
                excluded += 1
                continue
            try:
                report = CrawlerPresence.model_validate_json(entry.data).dispatch
            except ValueError:
                excluded += 1
                continue
            state = report.state
            if state == 'ready' and (report.valid_until is None or report.checked_at is None
                    or not report.checked_at <= now < report.valid_until
                    or (report.valid_until - report.checked_at).total_seconds() > 5):
                state = 'unknown'
            if state == 'blocked' and (report.reason is None or report.checked_at is None
                    or not 0 <= (now - report.checked_at).total_seconds() <= 35):
                state = 'unknown'
            if state == 'checking' and (report.checked_at is None
                    or not 0 <= (now - report.checked_at).total_seconds() <= 20):
                state = 'unknown'
            counts[state] += 1
            if state == 'blocked':
                reasons.add(report.reason)
        total = sum(counts.values())
        return CrawlerActivity(as_of=now, state='observed' if total else 'no_recent_reports',
            reason=None if total else 'worker_availability_unknown', reported_workers=total,
            ready_workers=counts['ready'], blocked_workers=counts['blocked'], checking_workers=counts['checking'],
            unknown_workers=counts['unknown'], waiting_reasons=sorted(reasons), excluded_reports=excluded, more_workers=len(keys) > 128)
