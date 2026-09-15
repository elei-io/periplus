"""Postgres owns result associations; bounded lookups attach public capture facts."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID
from sqlalchemy import select, tuple_, func, and_, or_
from periplus.crawl.control.collections.models import (
    CollectionRecord,
    CollectionResultRecord as Result,
)
from periplus.crawl.control.collections.history import (
    HistoryUnavailable,
    CollectionHistoryPage,
    HistoricalCollectionSummary,
    HistoryCursor,
    decode_cursor,
)
from periplus.crawl.control.collections.arrivals import (
    ArrivalCursor,
    CollectionArrival,
    CollectionArrivalsPage,
    decode_arrival_cursor,
)
from periplus.crawl.control.collections.lineage import (
    LineageCursor,
    ObservationLineageItem,
    ObservationLineagePage,
    decode_lineage_cursor,
)
from periplus.materialization.readiness import ObservationReadiness, CollectionReadiness
from periplus.materialization.rebuilds.models import (
    BuildRecord,
    PublicationRecord,
    RangeRecord,
)
from periplus.platform.clickhouse import ClickHouseClient
from periplus.retention.models import CaptureRetirementRecord


def _cursor(value):
    return (
        base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")
    )


def _wanted(identities):
    if len(identities) > 100:
        raise ValueError("Capture lookup exceeds 100 identities")
    return ",".join(f"{{id{i}:UUID}}" for i in range(len(identities))), {
        f"id{i}": str(v) for i, v in enumerate(identities)
    }


class CrawlResults:
    def __init__(self, client: ClickHouseClient, sessions):
        self.client, self.sessions = client, sessions
        self._slot = asyncio.Semaphore(1)
        self._pending = 0

    async def close(self):
        async with self._slot:
            await asyncio.to_thread(self.client.close)

    async def _read(self, sql, parameters=None):
        if self._pending >= 8:
            raise HistoryUnavailable("Corpus reader is busy")
        self._pending += 1
        acquired = False
        try:
            await asyncio.wait_for(self._slot.acquire(), timeout=2)
            acquired = True
            task = asyncio.create_task(
                asyncio.to_thread(self.client.query, sql, parameters=parameters)
            )
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
            for column in result.get("meta", []):
                if "DateTime" in column["type"]:
                    for row in result["data"]:
                        value = row.get(column["name"])
                        if isinstance(value, str):
                            row[column["name"]] = (
                                datetime.fromisoformat(value)
                                .replace(tzinfo=UTC)
                                .isoformat()
                            )
            return result
        except Exception as exc:
            raise HistoryUnavailable("Corpus is unavailable") from exc
        finally:
            if acquired:
                self._slot.release()
            self._pending -= 1

    async def material_database(self):
        def selected():
            with self.sessions() as session:
                publication = session.get(PublicationRecord, "public_v1")
                return session.get(BuildRecord, publication.build_id).material_database

        return await asyncio.to_thread(selected)

    async def captures(self, identities):
        if not identities:
            return {}
        wanted, parameters = _wanted(list(dict.fromkeys(identities)))
        database = await self.material_database()
        rows = (
            await self._read(
                f"SELECT capture_id, requested_url, effective_url, captured_at, http_status FROM {database}.captures "
                f"WHERE capture_id IN ({wanted})",
                parameters,
            )
        )["data"]
        return {UUID(row["capture_id"]): row for row in rows}

    async def readiness(self, identities):
        rows = await self.captures(identities)
        return {
            identity: ObservationReadiness(
                observation_id=identity,
                query_ready=identity in rows,
                reason="materialization_committed"
                if identity in rows
                else "materialization_pending",
                as_of=datetime.now(UTC),
            )
            for identity in identities
        }

    async def collection_readiness(self, identities):
        if len(identities) > 100:
            raise ValueError("Collection readiness exceeds 100 identities")

        def read():
            with self.sessions() as session:
                publication = session.get(PublicationRecord, "public_v1")
                ranges = list(
                    session.scalars(
                        select(RangeRecord).where(
                            RangeRecord.build_id == publication.build_id
                        )
                    )
                )
                covered = (
                    or_(
                        *[
                            and_(
                                Result.archive_shard == r.shard,
                                or_(
                                    Result.archive_sequence <= r.cursor,
                                    and_(
                                        Result.archive_sequence > r.upper,
                                        Result.archive_sequence <= r.live_cursor,
                                    ),
                                ),
                            )
                            for r in ranges
                        ]
                    )
                    if ranges
                    else False
                )
                covered = and_(
                    covered,
                    ~select(CaptureRetirementRecord.capture_id)
                    .where(CaptureRetirementRecord.capture_id == Result.capture_id)
                    .exists(),
                )
                counts = {
                    row[0]: (row[1], row[2])
                    for row in session.execute(
                        select(
                            Result.collection_id,
                            func.count(),
                            func.count().filter(covered),
                        )
                        .where(Result.collection_id.in_(identities))
                        .group_by(Result.collection_id)
                    )
                }
                records = list(
                    session.scalars(
                        select(CollectionRecord).where(
                            CollectionRecord.id.in_(identities)
                        )
                    )
                )
                result = {}
                for collection in records:
                    expected = collection.supplied_pages + collection.failed_pages
                    count, ready = counts.get(collection.id, (0, 0))
                    complete = (
                        collection.status == "settled" and expected == count == ready
                    )
                    result[collection.id] = CollectionReadiness(
                        collection_id=collection.id,
                        query_ready=complete,
                        reason="materialization_committed"
                        if complete
                        else "archive_or_materialization_pending",
                        as_of=datetime.now(UTC),
                    )
                return result

        return await asyncio.to_thread(read)

    async def list(self, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError("history page outside bounds")
        anchor = decode_cursor(cursor)

        def read():
            with self.sessions() as session:
                statement = select(CollectionRecord).where(
                    CollectionRecord.status == "settled"
                )
                if anchor:
                    statement = statement.where(
                        tuple_(CollectionRecord.created_at, CollectionRecord.id)
                        < (anchor.requested_at, anchor.id)
                    )
                records = list(
                    session.scalars(
                        statement.order_by(
                            CollectionRecord.created_at.desc(),
                            CollectionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                )
                items = [
                    HistoricalCollectionSummary(
                        id=r.id,
                        request_class=r.spec["request_class"],
                        summary=str(
                            r.spec.get("seed_description")
                            or ", ".join(r.spec.get("seed_urls", []))
                            or r.spec.get("seed_sql")
                            or "Collection"
                        )[:500],
                        created_at=r.created_at,
                        completed_at=r.completed_at,
                        outcome=r.outcome,
                        consumed_pages=r.consumed,
                        supplied_pages=r.supplied_pages,
                        failed_pages=r.failed_pages,
                    )
                    for r in records[:limit]
                ]
                following = (
                    _cursor(
                        HistoryCursor(
                            requested_at=items[-1].created_at, id=items[-1].id
                        )
                    )
                    if len(records) > limit
                    else None
                )
                return CollectionHistoryPage(
                    items=items, next_cursor=following, as_of=datetime.now(UTC)
                )

        return await asyncio.to_thread(read)

    async def arrivals(self, identity, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError("Arrival page outside bounds")
        anchor = decode_arrival_cursor(cursor, identity)

        def read():
            with self.sessions() as session:
                if session.get(CollectionRecord, identity) is None:
                    return None
                statement = select(Result).where(Result.collection_id == identity)
                if anchor:
                    statement = statement.where(
                        tuple_(Result.recorded_at, Result.id)
                        < (anchor.decided_at, anchor.fulfillment_id)
                    )
                return list(
                    session.scalars(
                        statement.order_by(
                            Result.recorded_at.desc(), Result.id.desc()
                        ).limit(limit + 1)
                    )
                )

        rows = await asyncio.to_thread(read)
        if rows is None:
            return None
        captures = await self.captures([row.capture_id for row in rows[:limit]])
        items = []
        for row in rows[:limit]:
            capture = captures.get(row.capture_id, {})
            context = row.selection_context
            items.append(
                CollectionArrival(
                    fulfillment_id=row.id,
                    observation_id=row.capture_id,
                    requested_url=row.requested_url,
                    parent_observation_id=context.get("parent_observation_id"),
                    depth=context["depth"],
                    rule_id=context["rule_id"],
                    mode=row.mode,
                    decided_at=row.recorded_at,
                    observation_committed=row.archived_at is not None,
                    effective_url=capture.get("effective_url"),
                    observed_at=capture.get("captured_at"),
                    outcome=row.outcome,
                    http_status_code=capture.get("http_status"),
                    query_ready=bool(capture),
                    query_readiness_reason="materialization_committed"
                    if capture
                    else "materialization_pending",
                )
            )
        following = (
            _cursor(
                ArrivalCursor(
                    collection_id=identity,
                    decided_at=items[-1].decided_at,
                    fulfillment_id=items[-1].fulfillment_id,
                )
            )
            if len(rows) > limit
            else None
        )
        return CollectionArrivalsPage(
            collection_id=identity,
            items=items,
            next_cursor=following,
            as_of=datetime.now(UTC),
        )

    async def observation_lineage(self, identity, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError("Lineage page outside bounds")
        anchor = decode_lineage_cursor(cursor, identity)

        def read():
            with self.sessions() as session:
                statement = select(Result).where(Result.capture_id == identity)
                if anchor:
                    statement = statement.where(
                        tuple_(Result.recorded_at, Result.id)
                        < (anchor.decided_at, anchor.record_id)
                    )
                return list(
                    session.scalars(
                        statement.order_by(
                            Result.recorded_at.desc(), Result.id.desc()
                        ).limit(limit + 1)
                    )
                )

        rows = await asyncio.to_thread(read)
        if not rows:
            captures = await self.captures([identity])
            if identity not in captures:
                return None
            return ObservationLineagePage(
                observation_id=identity,
                requested_url=captures[identity]["requested_url"],
                items=[],
                next_cursor=None,
                as_of=datetime.now(UTC),
            )
        items = [
            ObservationLineageItem(
                record_id=row.id,
                kind="fulfillment",
                decided_at=row.recorded_at,
                collection_id=row.collection_id,
                parent_observation_id=row.selection_context.get(
                    "parent_observation_id"
                ),
                rule_id=row.selection_context["rule_id"],
                depth=row.selection_context["depth"],
                mode=row.mode,
                reason=None,
                policy_version=None,
            )
            for row in rows[:limit]
        ]
        following = (
            _cursor(
                LineageCursor(
                    observation_id=identity,
                    decided_at=items[-1].decided_at,
                    record_id=items[-1].record_id,
                    kind="fulfillment",
                )
            )
            if len(rows) > limit
            else None
        )
        return ObservationLineagePage(
            observation_id=identity,
            requested_url=rows[0].requested_url,
            items=items,
            next_cursor=following,
            as_of=datetime.now(UTC),
        )

    async def live(self, *, now):
        from periplus.crawl.runtime.live import (
            HistoricalActivity,
            VelocityWindow,
            RecentCapture,
            count_attempt_starts,
        )
        from periplus.crawl.runtime.frontier_models import (
            AcquisitionRecord as Acquisition,
        )

        def read():
            with self.sessions() as session:
                velocities = []
                for seconds in (60, 300):
                    since = now - timedelta(seconds=seconds)
                    attempts = count_attempt_starts(session, since=since, until=now)
                    success = session.scalar(
                        select(func.count())
                        .select_from(Acquisition)
                        .where(
                            Acquisition.completed_at >= since,
                            Acquisition.status == "succeeded",
                        )
                    )
                    failed = session.scalar(
                        select(func.count())
                        .select_from(Acquisition)
                        .where(
                            Acquisition.completed_at >= since,
                            Acquisition.status == "failed",
                        )
                    )
                    fulfilled = session.scalar(
                        select(func.count())
                        .select_from(Result)
                        .where(Result.recorded_at >= since)
                    )
                    velocities.append(
                        VelocityWindow(
                            seconds=seconds,
                            domain=None,
                            attempt_starts=attempts,
                            successful_captures=success,
                            failed_captures=failed,
                            fulfillments=fulfilled,
                            attempt_starts_per_minute=attempts * 60 / seconds,
                        )
                    )
                recent = list(
                    session.scalars(
                        select(Acquisition)
                        .where(Acquisition.status == "succeeded")
                        .order_by(Acquisition.completed_at.desc())
                        .limit(5)
                    )
                )
                return velocities, recent

        velocities, recent = await asyncio.to_thread(read)
        proofs = await self.readiness([row.id for row in recent])
        return HistoricalActivity(
            as_of=datetime.now(UTC),
            window_end=now,
            velocities=velocities,
            recent=[
                RecentCapture(
                    observation_id=row.id,
                    requested_url=row.url,
                    completed_at=row.completed_at,
                    evidence_committed=row.evidence_committed_at is not None,
                    query_ready=proofs[row.id].query_ready,
                    query_readiness_reason=proofs[row.id].reason,
                )
                for row in recent
            ],
        )
