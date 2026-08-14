"""Notebook-friendly Periplus crawl resources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any, AsyncIterator, Collection, Literal
from uuid import UUID

from ._http import request
from .errors import CrawlFailed, WaitTimeout

CrawlStatus = Literal[
    "queued",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
]
RelationScope = Literal[
    "same_origin",
    "same_host",
    "same_site",
    "external",
]
_TERMINAL = {
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
}


@dataclass(slots=True)
class Crawl:
    id: UUID
    urls: tuple[str, ...]
    plan_id: UUID
    status: CrawlStatus
    plan_slug: str | None = None
    max_crawls: int = 0
    crawl_limit_reached: bool = False
    request_count: int = 0
    pending_request_count: int = 0
    failed_request_count: int = 0
    error_count: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    _poll_seconds: float = field(default=0.5, repr=False)

    async def refresh(self) -> Crawl:
        _apply(self, _crawl(await request("GET", f"/graph-runs/{self.id}")))
        return self

    async def completed(self, *, timeout: float | None = None) -> Crawl:
        started = time.monotonic()
        while self.status not in _TERMINAL:
            _check_timeout(started, timeout, "crawl completion")
            await asyncio.sleep(self._poll_seconds)
            await self.refresh()
        return self

    async def pause(self) -> Crawl:
        _apply(
            self,
            _crawl(await request("POST", f"/graph-runs/{self.id}/pause")),
        )
        return self

    async def resume(self) -> Crawl:
        _apply(
            self,
            _crawl(await request("POST", f"/graph-runs/{self.id}/resume")),
        )
        return self

    async def cancel(self) -> Crawl:
        _apply(
            self,
            _crawl(await request("POST", f"/graph-runs/{self.id}/cancel")),
        )
        return self

    async def failures(self) -> list[dict[str, Any]]:
        payload = await request(
            "GET", f"/graph-runs/{self.id}/failure-summary"
        )
        return list(payload["items"])

    async def events(self) -> AsyncIterator[Crawl]:
        previous: tuple[CrawlStatus, int, int] | None = None
        while True:
            await self.refresh()
            marker = (
                self.status,
                self.request_count,
                self.pending_request_count,
            )
            if marker != previous:
                previous = marker
                yield self
            if self.status in _TERMINAL:
                return
            await asyncio.sleep(self._poll_seconds)

    def raise_for_status(self) -> None:
        if self.status in {
            "completed_with_errors",
            "failed",
            "cancelled",
        }:
            raise CrawlFailed(
                self.error or f"crawl {self.id} ended with status {self.status}"
            )


@dataclass(frozen=True, slots=True)
class CrawlPage:
    items: list[Crawl]
    next_cursor: str | None = None


async def run(
    urls: str | Collection[str],
    *,
    depth: int | None = None,
    relation_scope: RelationScope | None = None,
    plan: str | None = None,
    max_crawls: int = 1_000,
    max_run_seconds: int | None = None,
) -> Crawl:
    start_urls = [urls] if isinstance(urls, str) else [url for url in urls]
    if not start_urls:
        raise ValueError("at least one crawl start URL is required")
    payload: dict[str, Any] = {
        "urls": start_urls,
        "max_crawls": max_crawls,
    }
    if depth is not None:
        payload["depth"] = depth
    if relation_scope is not None:
        payload["relation_scope"] = relation_scope
    if plan is not None:
        payload["plan"] = plan
    if max_run_seconds is not None:
        payload["max_run_seconds"] = max_run_seconds
    submission = await request("POST", "/crawls/", json=payload)
    return await get(UUID(str(submission["run_id"])))


async def get(crawl_id: UUID | str) -> Crawl:
    return _crawl(await request("GET", f"/graph-runs/{crawl_id}"))


async def list(
    *,
    status: CrawlStatus | Collection[CrawlStatus] | None = None,
    plan: str | UUID | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> CrawlPage:
    if cursor is not None:
        raise ValueError("cursor pagination is not yet supported by Periplus API")
    payload = await request("GET", "/graph-runs/")
    statuses = (
        {status}
        if isinstance(status, str)
        else set(status or ())
    )
    values = [_crawl(item) for item in payload["items"]]
    filtered = [
        crawl
        for crawl in values
        if (not statuses or crawl.status in statuses)
        and (
            plan is None
            or str(crawl.plan_id) == str(plan)
            or crawl.plan_slug == str(plan)
        )
        and (
            created_after is None
            or (
                crawl.created_at is not None
                and crawl.created_at >= created_after
            )
        )
        and (
            created_before is None
            or (
                crawl.created_at is not None
                and crawl.created_at < created_before
            )
        )
    ]
    return CrawlPage(items=filtered[:limit])


def _crawl(payload: dict[str, Any]) -> Crawl:
    trigger_urls = payload.get("trigger_urls") or ()
    return Crawl(
        id=UUID(str(payload["id"])),
        urls=tuple(str(url) for url in trigger_urls),
        plan_id=UUID(str(payload.get("plan_id") or payload["graph_id"])),
        plan_slug=payload.get("plan_slug"),
        status=payload["status"],
        max_crawls=int(payload.get("max_crawls", 0)),
        crawl_limit_reached=bool(payload.get("crawl_limit_reached", False)),
        request_count=int(payload.get("request_count", 0)),
        pending_request_count=int(payload.get("pending_request_count", 0)),
        failed_request_count=int(payload.get("failed_request_count", 0)),
        error_count=int(payload.get("error_count", 0)),
        created_at=_datetime(payload.get("created_at")),
        started_at=_datetime(payload.get("started_at")),
        completed_at=_datetime(payload.get("completed_at")),
        error=payload.get("error"),
    )


def _apply(target: Crawl, source: Crawl) -> None:
    poll_seconds = target._poll_seconds
    for field_name in target.__dataclass_fields__:
        if field_name != "_poll_seconds":
            setattr(target, field_name, getattr(source, field_name))
    target._poll_seconds = poll_seconds


def _datetime(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _check_timeout(
    started: float,
    timeout: float | None,
    label: str,
) -> None:
    if timeout is not None and time.monotonic() - started >= timeout:
        raise WaitTimeout(f"timed out waiting for {label}")
