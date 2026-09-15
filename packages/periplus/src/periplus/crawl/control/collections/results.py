"""Bounded analytical crawl reads; durable collection intent stays in Postgres."""
import asyncio
import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, tuple_

from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.history import (
    HistoryUnavailable, CollectionHistoryPage, HistoricalCollectionSummary, HistoryCursor, decode_cursor,
)
from periplus.crawl.control.collections.arrivals import (
    ArrivalCursor, CollectionArrival, CollectionArrivalsPage, decode_arrival_cursor,
)
from periplus.crawl.control.collections.lineage import (
    LineageCursor, ObservationLineageItem, ObservationLineagePage, decode_lineage_cursor,
)
from periplus.crawl.runtime.frontier_views import collection_views
from periplus.materialization.readiness import ObservationReadiness, CollectionReadiness
from periplus.platform.clickhouse import ClickHouseClient
from periplus.retention.identities import retired

_READY = """SELECT i.visit_id FROM ingest.visits i
    INNER JOIN material.visit_results r ON i.visit_id=r.visit_id AND i.evidence_sha256=r.evidence_sha256
    LEFT JOIN (SELECT content_sha256, 1 AS present FROM material.html_documents) d
        ON d.content_sha256=r.html_content_sha256
    WHERE r.html_content_sha256 IS NULL OR (d.present=1 AND i.content_sha256=r.html_content_sha256)"""


def _time(value):
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _cursor(value):
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip('=')


def _wanted(identities):
    if len(identities) > 100:
        raise ValueError('readiness reads allow at most 100 identities')
    return ','.join(f'{{id{index}:UUID}}' for index in range(len(identities))), {
        f'id{index}': str(value) for index, value in enumerate(identities)}


class CrawlResults:
    def __init__(self, client: ClickHouseClient, sessions):
        self.client, self.sessions = client, sessions
        self._slot = asyncio.Semaphore(1)

    async def close(self):
        async with self._slot:
            await asyncio.to_thread(self.client.close)

    async def _read(self, sql, parameters=None):
        try:
            async with self._slot:
                result = await asyncio.to_thread(self.client.query, sql, parameters=parameters)
                for column in result.get('meta', []):
                    if 'DateTime' in column['type']:
                        for row in result['data']:
                            value = row.get(column['name'])
                            if isinstance(value, str):
                                row[column['name']] = datetime.fromisoformat(value).replace(tzinfo=UTC).isoformat()
                return result
        except Exception as exc:
            raise HistoryUnavailable('Crawl results are unavailable') from exc

    async def is_retired(self, identity):
        return await asyncio.to_thread(retired, 'collection', str(identity))

    async def readiness(self, identities):
        identities = list(dict.fromkeys(identities))
        if not identities:
            return {}
        wanted, parameters = _wanted(identities)
        rows = (await self._read(f"SELECT visit_id, visit_id IN ({_READY}) AS ready "
            f"FROM ingest.visits WHERE visit_id IN ({wanted})", parameters))['data']
        by_id = {UUID(row['visit_id']): bool(row['ready']) for row in rows}
        now = datetime.now(UTC)
        return {identity: ObservationReadiness(observation_id=identity, query_ready=by_id.get(identity),
            reason='materialization_committed' if by_id.get(identity) else 'materialization_pending'
                if identity in by_id else 'observation_commit_not_verified', as_of=now) for identity in identities}

    async def collection_readiness(self, identities):
        identities = list(dict.fromkeys(identities))
        if not identities:
            return {}
        wanted, parameters = _wanted(identities)
        def controls():
            return {identity: collection_views(self.sessions, identity=identity) for identity in identities}
        views = await asyncio.to_thread(controls)
        rows = (await self._read(f"SELECT collection_id, count() AS n, uniqExact(record_id) AS unique_n, "
            f"countIf(observation_id IN ({_READY})) AS ready FROM ingest.fulfillments "
            f"WHERE collection_id IN ({wanted}) GROUP BY collection_id", parameters))['data']
        counts = {UUID(row['collection_id']): row for row in rows}
        result = {}
        for identity in identities:
            current = views[identity]
            row = counts.get(identity, {'n': 0, 'unique_n': 0, 'ready': 0})
            expected = current[0].supplied_pages + current[0].failed_pages if current else None
            ready = bool(current and current[0].lineage_ready and current[0].ingested_pages == expected
                         and row['n'] == row['unique_n'] == row['ready'] == expected)
            result[identity] = CollectionReadiness(collection_id=identity, query_ready=ready,
                reason='materialization_committed' if ready else 'materialization_or_ingestion_pending', as_of=datetime.now(UTC))
        return result

    async def list(self, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError('history page outside bounds')
        anchor = decode_cursor(cursor)
        def read():
            with self.sessions() as session:
                statement = select(CollectionRecord).where(CollectionRecord.status == 'settled')
                if anchor:
                    statement = statement.where(tuple_(CollectionRecord.created_at, CollectionRecord.id)
                                                < (anchor.requested_at, anchor.id))
                records = list(session.scalars(statement.order_by(CollectionRecord.created_at.desc(),
                    CollectionRecord.id.desc()).limit(limit+1)))
                items = [HistoricalCollectionSummary(id=r.id, request_class=r.spec['request_class'],
                    summary=str(r.spec.get('seed_description') or ', '.join(r.spec.get('seed_urls', []))
                                or r.spec.get('seed_sql') or 'Collection')[:500],
                    created_at=r.created_at, completed_at=r.completed_at, outcome=r.outcome,
                    consumed_pages=r.consumed, supplied_pages=r.supplied_pages, failed_pages=r.failed_pages)
                    for r in records[:limit]]
                following = _cursor(HistoryCursor(requested_at=items[-1].created_at, id=items[-1].id)) if len(records)>limit else None
                return CollectionHistoryPage(items=items, next_cursor=following, as_of=datetime.now(UTC))
        return await asyncio.to_thread(read)

    async def arrivals(self, identity, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError('arrival page outside bounds')
        anchor = decode_arrival_cursor(cursor, identity)
        def exists():
            with self.sessions() as session:
                return session.get(CollectionRecord, identity) is not None
        if not await asyncio.to_thread(exists):
            return None
        parameters = {'id': str(identity)}
        predicate = ''
        if anchor:
            predicate = ' AND (f.recorded_at,f.record_id)<({at:DateTime64(6)}, {record:UUID})'
            parameters.update(at=_time(anchor.decided_at), record=str(anchor.fulfillment_id))
        rows = (await self._read(f"""SELECT f.record_id AS fulfillment_id, f.observation_id,
            f.requested_url, f.parent_observation_id, f.depth, f.rule_id, f.mode,
            f.recorded_at AS decided_at, i.present=1 AS observation_committed,
            i.effective_url, i.observed_at, i.outcome, i.status_code AS http_status_code,
            f.observation_id IN ({_READY}) AS query_ready
            FROM ingest.fulfillments f LEFT JOIN (SELECT *,1 AS present FROM ingest.visits) i
            ON i.visit_id=f.observation_id WHERE f.collection_id={{id:UUID}} {predicate}
            ORDER BY f.recorded_at DESC,f.record_id DESC LIMIT {limit+1} SETTINGS join_use_nulls=1""", parameters))['data']
        items = [CollectionArrival(**{**row, 'observation_committed': bool(row['observation_committed']),
                 'query_ready': bool(row['query_ready']),
                 'query_readiness_reason': 'materialization_committed' if row['query_ready'] else 'materialization_pending'}) for row in rows[:limit]]
        following = _cursor(ArrivalCursor(collection_id=identity, decided_at=items[-1].decided_at,
            fulfillment_id=items[-1].fulfillment_id)) if len(rows)>limit else None
        return CollectionArrivalsPage(collection_id=identity, items=items, next_cursor=following, as_of=datetime.now(UTC))

    async def observation_lineage(self, identity, *, limit, cursor):
        if not 1 <= limit <= 100:
            raise ValueError('lineage page outside bounds')
        anchor = decode_lineage_cursor(cursor, identity)
        rows = (await self._read('SELECT requested_url FROM ingest.visits WHERE visit_id={id:UUID} LIMIT 2',
                                {'id': str(identity)}))['data']
        if not rows:
            return None
        if len(rows)!=1:
            raise HistoryUnavailable('Duplicate observation identity')
        parameters={'id':str(identity)}
        predicate=''
        if anchor:
            predicate='WHERE (decided_at,record_id,kind)<({at:DateTime64(6)}, {record:UUID}, {kind:String})'
            parameters.update(at=_time(anchor.decided_at), record=str(anchor.record_id), kind=anchor.kind)
        events=(await self._read(f"""WITH events AS (
            SELECT record_id,'fulfillment' AS kind,recorded_at AS decided_at,collection_id,
                parent_observation_id,rule_id,depth,mode,NULL AS reason,NULL AS policy_version
            FROM ingest.fulfillments WHERE observation_id={{id:UUID}}
            UNION ALL
            SELECT record_id,'reason',recorded_at,collection_id,parent_observation_id,rule_id,
                NULL,NULL,reason,policy_version FROM ingest.acquisition_reasons WHERE observation_id={{id:UUID}}
            ) SELECT * FROM events {predicate} ORDER BY decided_at DESC,record_id DESC,kind DESC LIMIT {limit+1}""",parameters))['data']
        items=[ObservationLineageItem(**row) for row in events[:limit]]
        following=_cursor(LineageCursor(observation_id=identity,decided_at=items[-1].decided_at,
            record_id=items[-1].record_id,kind=items[-1].kind)) if len(events)>limit else None
        return ObservationLineagePage(observation_id=identity,requested_url=rows[0]['requested_url'],
            items=items,next_cursor=following,as_of=datetime.now(UTC))

    async def live(self, *, now):
        from periplus.crawl.runtime.live import HistoricalActivity, VelocityWindow, RecentCapture
        rows = (await self._read("""WITH {now:DateTime64(6)} AS ending, ending-INTERVAL 300 SECOND AS beginning,
            events AS (
                SELECT requested_url, a.started_at AS event_at,'attempt' AS kind
                FROM ingest.visits ARRAY JOIN attempts AS a
                WHERE finished_at>=beginning AND a.started_at BETWEEN beginning AND ending
                UNION ALL
                SELECT requested_url,finished_at,outcome FROM ingest.visits
                WHERE finished_at BETWEEN beginning AND ending AND outcome IN ('succeeded','failed')
                UNION ALL
                SELECT requested_url,recorded_at,'fulfillment' FROM ingest.fulfillments
                WHERE recorded_at BETWEEN beginning AND ending
            ), counted AS (
                SELECT seconds,domain(requested_url) AS domain,grouping(domain) AS is_global,
                    countIf(kind='attempt') AS attempts,countIf(kind='succeeded') AS succeeded,
                    countIf(kind='failed') AS failed,countIf(kind='fulfillment') AS fulfilled
                FROM events ARRAY JOIN [60,300] AS seconds
                WHERE event_at>=ending-toIntervalSecond(seconds)
                GROUP BY GROUPING SETS ((seconds,domain),(seconds))
            ) SELECT * FROM counted
            QUALIFY is_global=1 OR row_number() OVER (PARTITION BY seconds,is_global ORDER BY attempts DESC,domain)<=10
            ORDER BY seconds,is_global DESC,attempts DESC,domain""", {'now': _time(now)}))['data']
        velocities=[]
        for row in rows:
            velocities.append(VelocityWindow(seconds=row['seconds'],domain=None if row['is_global'] else row['domain'],
                attempt_starts=row['attempts'],successful_captures=row['succeeded'],failed_captures=row['failed'],
                fulfillments=row['fulfilled'],attempt_starts_per_minute=row['attempts']*60/row['seconds']))
        for seconds in (60,300):
            if not any(value.domain is None and value.seconds==seconds for value in velocities):
                velocities.append(VelocityWindow(seconds=seconds,domain=None,attempt_starts=0,
                    successful_captures=0,failed_captures=0,fulfillments=0,attempt_starts_per_minute=0))
        recent=(await self._read('SELECT visit_id,requested_url,finished_at FROM ingest.visits '
            'WHERE outcome=\'succeeded\' AND finished_at<={now:DateTime64(6)} '
            'ORDER BY finished_at DESC,visit_id DESC LIMIT 5', {'now':_time(now)}))['data']
        proofs=await self.readiness([UUID(row['visit_id']) for row in recent])
        return HistoricalActivity(as_of=datetime.now(UTC),window_end=now,velocities=velocities,
            recent=[RecentCapture(observation_id=row['visit_id'],requested_url=row['requested_url'],
                completed_at=row['finished_at'],evidence_committed=True,
                query_ready=proofs[UUID(row['visit_id'])].query_ready,
                query_readiness_reason=proofs[UUID(row['visit_id'])].reason) for row in recent])
