"""Partition-aligned keyset reads and exact reconstruction of frozen evidence."""
from datetime import UTC, datetime
import json
from periplus.ingestion.storage import evidence_digest
from periplus.platform.catalogue.records import VisitEvidence, VisitRecord, DocumentRecord, AttemptRecord, StepRecord
from periplus.platform.clickhouse import ClickHouseClient

KEY = '(requested_url, finished_at, visit_id)'


def _time(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value is not None else None


def decode_visit(row: dict) -> VisitEvidence:
    visit = {key: row[key] for key in VisitRecord.model_fields}
    visit['capture_policy'] = json.loads(visit['capture_policy'])
    visit['archive_source'] = json.loads(visit['archive_source'])
    for key in ('admitted_at', 'started_at', 'observed_at', 'finished_at'):
        visit[key] = _time(visit[key])
    document = None
    if row['document_id'] is not None:
        document = {key: row[key] for key in DocumentRecord.model_fields if key not in ('attempt_id', 'observed_at')}
        document['attempt_id'] = row['document_attempt_id']
        document['observed_at'] = _time(row['document_observed_at'])
    attempts, steps = [], []
    for child in row['attempts']:
        attempt = {key: child[key] for key in AttemptRecord.model_fields if key != 'visit_id'}
        attempt['visit_id'] = row['visit_id']
        attempt['resource_usage'] = json.loads(attempt['resource_usage']) if attempt['resource_usage'] else None
        for key in ('started_at', 'finished_at'):
            attempt[key] = _time(attempt[key])
        attempts.append(attempt)
        for item in child['steps']:
            steps.append({**item, 'attempt_id': child['attempt_id'], 'parameters': json.loads(item['parameters']),
                          'started_at': _time(item['started_at'])})
    result = VisitEvidence(visit=VisitRecord(**visit), document=DocumentRecord(**document) if document else None,
                          attempts=tuple(AttemptRecord(**x) for x in attempts), steps=tuple(StepRecord(**x) for x in steps))
    if evidence_digest(result) != row['evidence_sha256']:
        raise ValueError('Stored visit does not reconstruct its immutable evidence digest')
    return result


def plan_ranges(client: ClickHouseClient) -> list[tuple[int, list]]:
    partitions = client.query("SELECT DISTINCT partition FROM system.parts WHERE active "
        "AND database='ingest' AND table='visits' ORDER BY partition")["data"]
    ranges = []
    for partition in partitions:
        month = int(partition['partition'])
        rows = client.query(f"SELECT requested_url, finished_at, visit_id FROM ingest.visits "
            f"WHERE toYYYYMM(finished_at)={month} ORDER BY requested_url DESC, finished_at DESC, visit_id DESC LIMIT 1")["data"]
        if rows:
            ranges.append((month, [rows[0][key] for key in ('requested_url', 'finished_at', 'visit_id')]))
    return ranges


def read_page(client: ClickHouseClient, month: int, upper: list, cursor: list | None, size: int) -> list[dict]:
    from sqlglot import exp
    def bound(value):
        return exp.Tuple(expressions=[exp.convert(x) for x in value]).sql(dialect='clickhouse')
    # Bounds are typed values from control state, encoded by the SQL AST rather
    # than interpolated as untrusted SQL. The order matches the source sorting key.
    predicate = f'toYYYYMM(finished_at)={int(month)} AND {KEY} <= {bound(upper)}'
    if cursor:
        predicate += f' AND {KEY} > {bound(cursor)}'
    sql = ('SELECT * REPLACE(lower(hex(content_sha256)) AS content_sha256, '
           'lower(hex(evidence_sha256)) AS evidence_sha256) FROM ingest.visits WHERE '
           + predicate + f' ORDER BY {KEY} LIMIT {int(size)}')
    return client.query(sql)['data']
