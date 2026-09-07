"""Finite collection requests over the shared crawler frontier."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from ._http import request
from .errors import ApiError, CollectionFailed, WaitTimeout
from .types import CollectionItemsPage, CollectionArrivalsPage, CollectionHistoryPage, CollectionSpec, CurrentCollection, HistoricalCollection

CollectionSnapshot = Annotated[CurrentCollection | HistoricalCollection, Field(discriminator="source")]
_SNAPSHOT = TypeAdapter(CollectionSnapshot)


@dataclass(slots=True)
class Collection:
    snapshot: CurrentCollection | HistoricalCollection

    @property
    def id(self) -> UUID:
        return self.snapshot.id

    @property
    def settled(self) -> bool:
        if isinstance(self.snapshot, CurrentCollection):
            return self.snapshot.status == "settled"
        return self.snapshot.outcome is not None

    def _apply(self, snapshot: CurrentCollection | HistoricalCollection) -> Collection:
        if snapshot.id != self.id:
            raise ValueError("collection response changed identity")
        self.snapshot = snapshot
        return self

    async def refresh(self) -> Collection:
        return self._apply(_SNAPSHOT.validate_python(await request("GET", f"/collections/{self.id}")))

    async def items(self, *, limit: int = 20, after: UUID | str | None = None) -> CollectionItemsPage:
        """Read current interests in identity order, independently of settlement."""
        return await items(self.id, limit=limit, after=after)

    async def arrivals(self, *, limit: int = 20, cursor: str | None = None) -> CollectionArrivalsPage:
        """Read durable results, including after current work has retired."""
        return await arrivals(self.id, limit=limit, cursor=cursor)

    async def wait(self, *, timeout: float | None = None, poll_seconds: float = 1) -> Collection:
        """Wait for collection settlement, independently of catalogue readiness.

        Timeout/cancellation only stops this local wait. Temporary 429/503 reads
        honor Retry-After; submission and control mutations are never retried.
        """
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("poll_seconds must be finite and positive")
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and nonnegative")
        if self.settled:
            return self
        try:
            async with asyncio.timeout(timeout):
                delay = poll_seconds
                while not self.settled:
                    await asyncio.sleep(delay)
                    delay = poll_seconds
                    try:
                        await self.refresh()
                    except ApiError as exc:
                        if exc.status_code not in {429, 503}:
                            raise
                        delay = max(poll_seconds, min(300, exc.retry_after_seconds if exc.retry_after_seconds is not None else 5))
        except TimeoutError as exc:
            raise WaitTimeout(f"timed out waiting for collection {self.id}") from exc
        return self

    async def _act(self, action: Literal["pause", "resume", "cancel"]) -> Collection:
        if isinstance(self.snapshot, HistoricalCollection):
            raise ValueError("historical collections cannot be controlled")
        return self._apply(CurrentCollection.model_validate(await request(
            "POST", f"/collections/{self.id}/actions", json={"action": action})))

    async def pause(self) -> Collection:
        return await self._act("pause")

    async def resume(self) -> Collection:
        return await self._act("resume")

    async def cancel(self) -> Collection:
        return await self._act("cancel")

    async def set_priority(self, priority: int) -> Collection:
        if isinstance(self.snapshot, HistoricalCollection):
            raise ValueError("historical collections cannot be reprioritized")
        if not -10 <= priority <= 10:
            raise ValueError("priority must be between -10 and 10")
        return self._apply(CurrentCollection.model_validate(await request(
            "PUT", f"/collections/{self.id}/priority", json={"priority": priority})))

    def raise_for_status(self) -> None:
        if not self.settled:
            raise ValueError("collection has not settled")
        if self.snapshot.outcome not in {"budget_reached", "eligible_links_exhausted"} or (self.snapshot.failed_pages or 0) > 0:
            raise CollectionFailed(f"collection {self.id}: {self.snapshot.outcome}; failed pages: {self.snapshot.failed_pages}")


@dataclass(frozen=True, slots=True)
class CollectionPage:
    items: tuple[Collection, ...]
    limit: int
    offset: int
    source: Literal["current"] = "current"


async def submit(specification: CollectionSpec, *, id: UUID | None = None, priority: int = 0) -> Collection:
    if not -10 <= priority <= 10:
        raise ValueError("priority must be between -10 and 10")
    payload = {"specification": specification.model_dump(mode="json"), "priority": priority}
    if id is not None:
        payload["id"] = str(id)
    snapshot = _SNAPSHOT.validate_python(await request("POST", "/collections", json=payload))
    if id is not None and snapshot.id != UUID(str(id)):
        raise ValueError("collection response changed identity")
    return Collection(snapshot)


async def get(id: UUID | str) -> Collection:
    identity = UUID(str(id))
    snapshot = _SNAPSHOT.validate_python(await request("GET", f"/collections/{identity}"))
    if snapshot.id != identity:
        raise ValueError("collection response changed identity")
    return Collection(snapshot)


async def list(*, status: Literal["active", "paused", "settled"] | None = None,
               limit: int = 20, offset: int = 0) -> CollectionPage:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError("collection page outside bounds")
    params = {"limit": limit, "offset": offset}
    if status is not None:
        params["status"] = status
    payload = await request("GET", "/collections", params=params)
    if payload["source"] != "current":
        raise ValueError("expected a current collection page")
    return CollectionPage(tuple(Collection(CurrentCollection.model_validate(item)) for item in payload["items"]),
                          limit=payload["limit"], offset=payload["offset"])


async def history(*, limit: int = 20, cursor: str | None = None) -> CollectionHistoryPage:
    if not 1 <= limit <= 100 or (cursor is not None and len(cursor) > 512):
        raise ValueError("history page outside bounds")
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return CollectionHistoryPage.model_validate(await request("GET", "/collections/history", params=params))


async def items(id: UUID | str, *, limit: int = 20, after: UUID | str | None = None) -> CollectionItemsPage:
    identity = UUID(str(id))
    if not 1 <= limit <= 100:
        raise ValueError("current item page outside bounds")
    params = {"limit": limit}
    if after is not None:
        params["after"] = str(UUID(str(after)))
    page = CollectionItemsPage.model_validate(await request("GET", f"/collections/{identity}/items", params=params))
    if page.collection_id != identity:
        raise ValueError("current item response changed collection identity")
    return page


async def arrivals(id: UUID | str, *, limit: int = 20, cursor: str | None = None) -> CollectionArrivalsPage:
    identity = UUID(str(id))
    if not 1 <= limit <= 100 or (cursor is not None and len(cursor) > 512):
        raise ValueError("arrival page outside bounds")
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    page = CollectionArrivalsPage.model_validate(await request("GET", f"/collections/{identity}/arrivals", params=params))
    if page.collection_id != identity:
        raise ValueError("arrival response changed collection identity")
    return page
