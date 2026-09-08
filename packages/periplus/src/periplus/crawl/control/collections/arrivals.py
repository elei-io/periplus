"""Bounded immutable fulfillment arrivals, independent of current frontier retention."""
import base64
from datetime import UTC, datetime
from threading import Timer
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from periplus.platform.catalogue.connection import _identifier


class ArrivalCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection_id: UUID
    decided_at: datetime
    fulfillment_id: UUID


class CollectionArrival(BaseModel):
    fulfillment_id: UUID
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    parent_observation_id: UUID | None
    depth: int = Field(ge=0)
    rule_id: str = Field(max_length=200)
    mode: Literal["acquired", "shared", "reused"]
    decided_at: datetime
    observation_committed: bool
    effective_url: str | None = Field(max_length=8192)
    observed_at: datetime | None
    outcome: str | None
    http_status_code: int | None
    query_ready: bool | None = None
    query_readiness_reason: str = "materialization_commit_not_verified"


class CollectionArrivalsPage(BaseModel):
    source: Literal["history"] = "history"
    collection_id: UUID
    definition_committed: bool = True
    items: list[CollectionArrival]
    next_cursor: str | None
    as_of: datetime


def decode_arrival_cursor(value: str | None, identity: UUID) -> ArrivalCursor | None:
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError("cursor too long")
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        cursor = ArrivalCursor.model_validate_json(raw)
        if cursor.collection_id != identity or cursor.decided_at.utcoffset() is None:
            raise ValueError("cursor identity or timezone mismatch")
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid collection arrival cursor") from exc


def read_arrivals(catalogue, identity: UUID, *, limit: int,
                  cursor: ArrivalCursor | None) -> CollectionArrivalsPage | None:
    if not 1 <= limit <= 100:
        raise ValueError("arrival page limit outside bounds")
    if cursor is not None and cursor.collection_id != identity:
        raise ValueError("cursor belongs to another collection")
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        definitions = connection.execute(f"""SELECT collection_id FROM {alias}.ingest.collections
            WHERE collection_id = ? LIMIT 2""",
            [identity]).fetchall()
        if not definitions:
            return None
        if len(definitions) != 1:
            raise ValueError("duplicate immutable collection identity")
        anchor = ""
        parameters = [identity]
        if cursor is not None:
            anchor = "AND (f.recorded_at < ? OR (f.recorded_at = ? AND f.record_id < ?))"
            parameters.extend([cursor.decided_at, cursor.decided_at, cursor.fulfillment_id])
        parameters.append(limit + 1)
        rows = connection.execute(f"""
            SELECT f.record_id, f.observation_id,
                   CASE WHEN length(f.requested_url) <= 8192 THEN f.requested_url ELSE NULL END,
                   f.parent_observation_id, f.depth,
                   CASE WHEN length(f.rule_id) <= 200 THEN f.rule_id ELSE NULL END,
                   f.mode, f.recorded_at, v.visit_id IS NOT NULL,
                   CASE WHEN length(v.effective_url) <= 8192 THEN v.effective_url ELSE NULL END,
                   v.observed_at, v.outcome, v.status_code,
                   coalesce(length(v.effective_url) > 8192, false)
            FROM {alias}.ingest.fulfillments f
            LEFT JOIN {alias}.ingest.visits v ON v.visit_id = f.observation_id
            WHERE f.collection_id = ?
              {anchor}
            ORDER BY f.recorded_at DESC, f.record_id DESC
            LIMIT ?
        """, parameters).fetchall()
        if len({row[0] for row in rows}) != len(rows) or any(row[13] for row in rows):
            raise ValueError("inconsistent or oversized arrival evidence")
        items = [CollectionArrival(fulfillment_id=row[0], observation_id=row[1], requested_url=row[2],
            parent_observation_id=row[3], depth=row[4], rule_id=row[5], mode=row[6], decided_at=row[7],
            observation_committed=row[8], effective_url=row[9], observed_at=row[10], outcome=row[11],
            http_status_code=row[12], query_readiness_reason="materialization_commit_not_verified" if row[8]
                else "observation_commit_not_verified") for row in rows[:limit]]
        from periplus.materialization.readiness import observation_readiness
        proofs = observation_readiness(catalogue, [item.observation_id for item in items])
        items = [item.model_copy(update={"query_ready": proofs[item.observation_id].query_ready,
            "query_readiness_reason": proofs[item.observation_id].reason}) if item.observation_committed else item
            for item in items]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            value = ArrivalCursor(collection_id=identity, decided_at=last.decided_at,
                                  fulfillment_id=last.fulfillment_id)
            next_cursor = base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")
        return CollectionArrivalsPage(collection_id=identity, items=items,
                                     next_cursor=next_cursor, as_of=datetime.now(UTC))
    finally:
        timer.cancel()
        timer.join()
