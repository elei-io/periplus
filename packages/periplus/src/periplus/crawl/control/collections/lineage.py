"""Bounded reverse provenance from immutable evidence, independent of retained work."""
import base64
from datetime import UTC, datetime
from threading import Timer
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from periplus.platform.catalogue.connection import _identifier


class LineageCursor(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    observation_id: UUID
    public_only: bool
    decided_at: AwareDatetime
    record_id: UUID
    kind: Literal['fulfillment', 'reason']


class ObservationLineageItem(BaseModel):
    record_id: UUID
    kind: Literal['fulfillment', 'reason']
    decided_at: datetime
    collection_id: UUID | None
    parent_observation_id: UUID | None
    rule_id: str = Field(max_length=200)
    depth: int | None = Field(ge=0)
    mode: Literal['acquired', 'shared', 'reused'] | None
    reason: Literal['collection', 'background'] | None
    policy_version: str | None = Field(max_length=200)


class ObservationLineagePage(BaseModel):
    observation_id: UUID
    requested_url: str = Field(max_length=8192)
    items: list[ObservationLineageItem]
    next_cursor: str | None
    as_of: datetime
    completeness: Literal['committed_visible_evidence_only_ingestion_may_lag'] = 'committed_visible_evidence_only_ingestion_may_lag'


def decode_lineage_cursor(value: str | None, identity: UUID, *, public_only: bool):
    if value is None:
        return None
    try:
        if len(value) > 512:
            raise ValueError('cursor too long')
        cursor = LineageCursor.model_validate_json(base64.b64decode(
            value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if cursor.observation_id != identity or cursor.public_only != public_only:
            raise ValueError('cursor belongs to another observation or visibility scope')
        return cursor
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid observation lineage cursor') from exc


def read_observation_lineage(catalogue, identity: UUID, *, public_only: bool, limit: int,
                             cursor: LineageCursor | None) -> ObservationLineagePage | None:
    if not 1 <= limit <= 100:
        raise ValueError('lineage page outside bounds')
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        observations = connection.execute(f"""SELECT visibility,
            CASE WHEN length(requested_url) <= 8192 THEN requested_url ELSE NULL END
            FROM {alias}.ingest.visits WHERE visit_id = ? AND (NOT ? OR visibility = 'public') LIMIT 2""",
            [identity, public_only]).fetchall()
        if not observations:
            return None
        if len(observations) != 1 or observations[0][1] is None:
            raise ValueError('observation identity or URL is inconsistent')
        visibility, requested_url = observations[0]
        parameters = [identity, visibility, identity, visibility]
        anchor = ''
        if cursor is not None:
            anchor = 'WHERE (decided_at, record_id, kind) < (?, ?::UUID, ?)'
            parameters.extend((cursor.decided_at, cursor.record_id, cursor.kind))
        parameters.append(limit + 1)
        rows = connection.execute(f"""
            WITH events AS (
                SELECT f.record_id, 'fulfillment' AS kind, f.recorded_at AS decided_at,
                       f.collection_id, p.visit_id AS parent_observation_id,
                       f.rule_id, f.depth, f.mode, NULL::VARCHAR AS reason, NULL::VARCHAR AS policy_version
                FROM {alias}.ingest.fulfillments f
                JOIN {alias}.ingest.collections d ON d.collection_id = f.collection_id AND d.visibility = f.visibility
                LEFT JOIN {alias}.ingest.visits p ON p.visit_id = f.parent_observation_id AND p.visibility = f.visibility
                WHERE f.observation_id = ? AND f.visibility = ?
                UNION ALL
                SELECT r.record_id, 'reason', r.recorded_at, r.collection_id, p.visit_id,
                       r.rule_id, NULL::INTEGER, NULL::VARCHAR, r.reason, r.policy_version
                FROM {alias}.ingest.acquisition_reasons r
                LEFT JOIN {alias}.ingest.collections d ON d.collection_id = r.collection_id AND d.visibility = r.visibility
                LEFT JOIN {alias}.ingest.visits p ON p.visit_id = r.parent_observation_id AND p.visibility = r.visibility
                WHERE r.observation_id = ? AND r.visibility = ?
                  AND (r.collection_id IS NULL OR d.collection_id IS NOT NULL)
            )
            SELECT record_id, kind, decided_at, collection_id, parent_observation_id,
                   CASE WHEN length(rule_id) <= 200 THEN rule_id ELSE NULL END,
                   depth, mode, reason,
                   CASE WHEN length(policy_version) <= 200 THEN policy_version ELSE NULL END,
                   coalesce(length(policy_version) > 200, false)
            FROM events {anchor} ORDER BY decided_at DESC, record_id DESC, kind DESC LIMIT ?
        """, parameters).fetchall()
        if len({(row[0], row[1]) for row in rows}) != len(rows) or any(row[10] for row in rows):
            raise ValueError('duplicate or oversized lineage evidence')
        items = [ObservationLineageItem(record_id=row[0], kind=row[1], decided_at=row[2],
            collection_id=row[3], parent_observation_id=row[4], rule_id=row[5], depth=row[6], mode=row[7],
            reason=row[8], policy_version=row[9]) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            value = LineageCursor(observation_id=identity, public_only=public_only, decided_at=last.decided_at,
                record_id=last.record_id, kind=last.kind)
            next_cursor = base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip('=')
        return ObservationLineagePage(observation_id=identity, requested_url=requested_url,
            items=items, next_cursor=next_cursor, as_of=datetime.now(UTC))
    finally:
        timer.cancel()
        timer.join()
