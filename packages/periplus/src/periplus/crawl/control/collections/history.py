"""Bounded immutable collection reads through the existing API catalogue owner."""
import asyncio
import base64
from datetime import UTC, datetime
import json
from threading import Timer
from typing import Literal
from uuid import UUID

from pydantic import computed_field, BaseModel, ConfigDict, Field

from periplus.crawl.control.collections.schemas import CollectionExecutionSpec, CollectionSpec
from periplus.platform.catalogue.connection import _identifier


class HistoricalCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["history"] = "history"
    id: UUID
    specification: CollectionExecutionSpec
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)
    seed_provenance: dict | None
    as_of: datetime
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"
    query_readiness_as_of: datetime | None = None
    query_generation_id: UUID | None = None


    @computed_field
    @property
    def expires_at(self) -> datetime | None:
        from periplus.retention.policy import expires_at
        return expires_at(self.specification.retention_seconds, self.completed_at)

    @computed_field
    @property
    def retention_expired(self) -> bool:
        expiry = self.expires_at
        return expiry is not None and expiry <= self.as_of


class HistoryUnavailable(RuntimeError):
    pass


def read_collection(catalogue, identity: UUID) -> HistoricalCollection | None:
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        rows = connection.execute(f"""
            SELECT d.collection_id,
                   CASE WHEN octet_length(encode(CAST(d.specification AS VARCHAR))) <= 1048576 THEN d.specification ELSE NULL END, d.recorded_at,
                   o.recorded_at, o.outcome, o.consumed_pages, o.supplied_pages,
                   o.failed_pages,
                   CASE WHEN octet_length(encode(CAST(o.seed_provenance AS VARCHAR))) <= 1048576 THEN o.seed_provenance ELSE NULL END,
                   octet_length(encode(CAST(o.seed_provenance AS VARCHAR)))
            FROM {alias}.ingest.collections d
            LEFT JOIN {alias}.ingest.collection_outcomes o
              ON o.collection_id = d.collection_id
            WHERE d.collection_id = ?
            LIMIT 2
        """, [identity]).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("duplicate immutable collection identity")
        row = rows[0]
        if row[9] is not None and row[9] > 1048576:
            raise ValueError("collection provenance exceeds its read bound")
        specification = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        if not isinstance(specification, dict):
            raise ValueError("invalid historical collection specification")
        provenance = json.loads(row[8]) if isinstance(row[8], str) else row[8]
        return HistoricalCollection(id=row[0], specification=specification, created_at=row[2],
            completed_at=row[3], outcome=row[4], consumed_pages=row[5], supplied_pages=row[6],
            failed_pages=row[7], seed_provenance=provenance, as_of=datetime.now(UTC))
    finally:
        timer.cancel()
        timer.join()


class CollectionHistory:
    def __init__(self, catalogue_control):
        self.control = catalogue_control
        self.slot = asyncio.Lock()
        self.pending_reads = 0

    async def is_retired(self, identity: UUID) -> bool:
        from periplus.retention.identities import retired
        return await self._read(lambda catalogue: retired(catalogue, "collection", str(identity)))

    async def get(self, identity: UUID) -> HistoricalCollection | None:
        return await self._read(lambda catalogue: read_collection(catalogue, identity))

    async def list(self, *, limit: int, cursor: str | None):
        anchor = decode_cursor(cursor)
        return await self._read(lambda catalogue: read_history_page(catalogue, limit=limit, cursor=anchor))

    async def live(self, *, now: datetime):
        from periplus.crawl.runtime.live import read_live_history
        return await self._read(lambda catalogue: read_live_history(catalogue, now=now))

    async def collection_readiness(self, identities: list[UUID]):
        from periplus.materialization.readiness import collection_readiness
        if len(identities) > 100:
            raise ValueError("readiness reads allow at most 100 collections")
        if not identities:
            return {}
        return await self._read(lambda catalogue: collection_readiness(catalogue, identities))

    async def readiness(self, identities: list[UUID]):
        from periplus.materialization.readiness import observation_readiness
        if len(identities) > 100:
            raise ValueError("readiness reads allow at most 100 observations")
        if not identities:
            return {}
        return await self._read(lambda catalogue: observation_readiness(catalogue, identities))

    async def observation_lineage(self, identity: UUID, *, limit: int, cursor: str | None):
        from periplus.crawl.control.collections.lineage import decode_lineage_cursor, read_observation_lineage
        if not 1 <= limit <= 100:
            raise ValueError("lineage page outside bounds")
        anchor = decode_lineage_cursor(cursor, identity)
        return await self._read(lambda catalogue: read_observation_lineage(catalogue, identity,
            limit=limit, cursor=anchor))

    async def arrivals(self, identity: UUID, *, limit: int, cursor: str | None):
        from periplus.crawl.control.collections.arrivals import decode_arrival_cursor, read_arrivals
        if not 1 <= limit <= 100:
            raise ValueError("arrival page limit outside bounds")
        anchor = decode_arrival_cursor(cursor, identity)
        return await self._read(lambda catalogue: read_arrivals(catalogue, identity,
            limit=limit, cursor=anchor))

    async def _read(self, operation):
        # A details page reads collection, items and arrivals concurrently. Let a
        # bounded local group serialize instead of manufacturing an outage for
        # every overlapping request. Admission and wait time are both bounded.
        if self.pending_reads >= 8:
            raise HistoryUnavailable("collection history is busy")
        self.pending_reads += 1
        acquired = False
        try:
            try:
                async with asyncio.timeout(2):
                    await self.slot.acquire()
                    acquired = True
            except TimeoutError as exc:
                raise HistoryUnavailable("collection history is busy") from exc
            task = asyncio.create_task(self.control.run(
                lambda _session, catalogue: operation(catalogue)))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
            except Exception as exc:
                raise HistoryUnavailable("collection history is unavailable") from exc
        finally:
            if acquired:
                self.slot.release()
            self.pending_reads -= 1


class HistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requested_at: datetime
    id: UUID


class HistoricalCollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    request_class: Literal["public", "system", "admin"]
    summary: str = Field(max_length=500)
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    consumed_pages: int | None = Field(ge=0)
    supplied_pages: int | None = Field(ge=0)
    failed_pages: int | None = Field(ge=0)


class CollectionHistoryPage(BaseModel):
    source: Literal["history"] = "history"
    items: list[HistoricalCollectionSummary]
    next_cursor: str | None
    as_of: datetime


def decode_cursor(value: str | None) -> HistoryCursor | None:
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor too long")
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        cursor = HistoryCursor.model_validate_json(decoded)
        if cursor.requested_at.utcoffset() is None:
            raise ValueError("cursor time requires a timezone")
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid collection history cursor") from exc


def read_history_page(catalogue, *, limit: int, cursor: HistoryCursor | None) -> CollectionHistoryPage:
    if not 1 <= limit <= 100:
        raise ValueError("history page limit outside bounds")
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        anchor = ""
        parameters = []
        if cursor is not None:
            anchor = "AND (d.recorded_at < ? OR (d.recorded_at = ? AND d.collection_id < ?))"
            parameters.extend((cursor.requested_at, cursor.requested_at, cursor.id))
        parameters.append(limit + 1)
        rows = connection.execute(f"""
            SELECT d.collection_id, json_extract_string(d.specification, '$.request_class'),
                   CASE WHEN octet_length(encode(CAST(d.specification AS VARCHAR))) <= 1048576
                     THEN left(coalesce(nullif(json_extract_string(d.specification, '$.seed_description'), ''),
                                        json_extract_string(d.specification, '$.seed_urls[0]'), 'Corpus selection'), 500)
                     ELSE NULL END,
                   d.recorded_at, o.recorded_at, o.outcome, o.consumed_pages, o.supplied_pages, o.failed_pages,
                   CASE WHEN octet_length(encode(CAST(d.specification AS VARCHAR))) <= 1048576
                     THEN json_extract_string(d.specification, '$.request_class')
                     ELSE NULL END
            FROM {alias}.ingest.collections d
            LEFT JOIN {alias}.ingest.collection_outcomes o
              ON o.collection_id = d.collection_id
            WHERE true {anchor}
            ORDER BY d.recorded_at DESC, d.collection_id DESC
            LIMIT ?
        """, parameters).fetchall()
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError("duplicate immutable collection identity")
        for row in rows:
            if row[2] is None or row[9] is None:
                raise ValueError("invalid or oversized historical collection specification")
            if row[9] != row[1]:
                raise ValueError("historical request class is inconsistent")
        items = [HistoricalCollectionSummary(id=row[0], request_class=row[1], summary=row[2], created_at=row[3],
            completed_at=row[4], outcome=row[5], consumed_pages=row[6], supplied_pages=row[7], failed_pages=row[8])
            for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            encoded = HistoryCursor(requested_at=last.created_at, id=last.id).model_dump_json().encode()
            next_cursor = base64.urlsafe_b64encode(encoded).decode().rstrip("=")
        return CollectionHistoryPage(items=items, next_cursor=next_cursor, as_of=datetime.now(UTC))
    finally:
        timer.cancel()
        timer.join()
