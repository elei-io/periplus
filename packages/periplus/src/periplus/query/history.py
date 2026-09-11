"""Bounded best-effort history delivery; no storage credentials in the query process."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
import time
from uuid import UUID, uuid4

import httpx
import sqlglot
from sqlglot import exp
from prometheus_client import Counter
from periplus.operations.query_history.schemas import Execution
from periplus.platform.telemetry import event

_delivery = Counter('periplus_query_history_delivery_total', 'Best-effort terminal history delivery.', ('outcome',))
_record_slots = asyncio.Semaphore(8)
VERSION = f"sqlglot-{sqlglot.__version__}-v1"


def shape(sql):
    empty = dict(query_template=None, query_fingerprint=None, relations=[], functions=[], features={})
    try:
        statements = sqlglot.parse(sql, read='duckdb', error_level=sqlglot.ErrorLevel.RAISE)
        if not statements or any(s is None or any(s.find_all(exp.Command)) for s in statements):
            return empty
        relations, functions = set(), set()
        features = dict(joins=0, ctes=0, aggregations=0, windows=0, subqueries=0)
        for statement in statements:
            ctes = {c.alias_or_name for c in statement.find_all(exp.CTE)}
            relations.update(exp.Table(this=t.this.copy(), db=t.args.get('db'), catalog=t.args.get('catalog')).sql(
                                 dialect='duckdb', comments=False, normalize=True) for t in statement.find_all(exp.Table)
                             if not (not t.db and t.name in ctes) and isinstance(t.this, exp.Identifier))
            functions.update(f.name.lower() if isinstance(f, exp.Anonymous) else f.sql_name().lower()
                             for f in statement.find_all(exp.Func))
            for key, cls in [('joins', exp.Join), ('ctes', exp.CTE), ('aggregations', exp.AggFunc),
                             ('windows', exp.Window), ('subqueries', exp.Subquery)]:
                features[key] += len(list(statement.find_all(cls)))
            for node in list(statement.walk()):
                node.comments = None
                if isinstance(node, (exp.Literal, exp.Boolean, exp.Null, exp.Placeholder, exp.Parameter)):
                    node.replace(exp.Placeholder())
        template = '; '.join(s.sql(dialect='duckdb', comments=False, normalize=True) for s in statements)
        return dict(query_template=template, query_fingerprint=hashlib.sha256((VERSION+'\n'+template).encode()).hexdigest(),
                    relations=sorted(relations), functions=sorted(functions), features=features)
    except Exception:
        # Never log parser errors, which include the submitted SQL.
        return empty


class HistoryClient:
    def __init__(self):
        self.url = os.environ.get('PERIPLUS_API_URL', 'http://127.0.0.1:8000').rstrip('/')
        self.client = httpx.AsyncClient(timeout=1, limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
        self.slots = asyncio.Semaphore(8)

    async def close(self):
        await self.client.aclose()

    async def record(self, value):
        if self.slots.locked():
            _delivery.labels('dropped').inc()
            return
        try:
            async with self.slots, asyncio.timeout(1):
                response = await self.client.post(self.url+'/internal/query-history',
                    headers={'Authorization': 'Bearer '+os.environ.get('PERIPLUS_QUERY_API_TOKEN', ''), 'Content-Type': 'application/json'},
                    content=value.model_dump_json())
                response.raise_for_status()
            _delivery.labels('stored').inc()
        except Exception:
            _delivery.labels('failed').inc()
            event('query_history_delivery_failed', operation_id=str(value.execution_id))


@asynccontextmanager
async def track(request, payload, operation, *, source=None):
    started = datetime.now(UTC)
    clock = time.monotonic()
    try:
        request_id = UUID(str(getattr(request.state, 'request_id', '')))
    except ValueError:
        request_id = uuid4()
    source = source or request.headers.get('x-periplus-query-source', 'unknown')
    if source not in {'public_console', 'assistant', 'sdk', 'internal', 'admin'}:
        source = 'unknown'
    record = dict(execution_id=uuid4(), request_id=request_id, started_at=started,
                  source=source, operation=operation, sql_text=payload.sql, parameters=payload.parameters,
                  fingerprint_version=VERSION, outcome='failed', error_code='internal_error',
                  service_version=os.environ.get('PERIPLUS_SERVICE_VERSION'))
    try:
        yield record
    except asyncio.CancelledError:
        record.update(outcome='cancelled', error_code='request_cancelled')
        raise
    finally:
        record.update(finished_at=datetime.now(UTC), elapsed_ms=(time.monotonic()-clock)*1000)
        recorder = getattr(request.app.state, 'query_history', None)
        if recorder is not None and _record_slots.locked():
            _delivery.labels('dropped').inc()
        elif recorder is not None:
            try:
                # Parser work and serialization never hold the query admission slot.
                async with _record_slots:
                    record.update(await asyncio.to_thread(shape, payload.sql))
                    await recorder.record(Execution(**record))
            except Exception:
                _delivery.labels('failed').inc()
                event('query_history_record_failed', operation_id=str(record['execution_id']))


def result_fields(result):
    values = dict(outcome='success', error_code=None)
    if hasattr(result, 'query_id'):
        values['execution_id'] = UUID(result.query_id)
    if hasattr(result, 'rows'):
        values.update(result_rows=getattr(result, 'row_count', len(result.rows)), truncated=result.truncated,
            result_bytes=getattr(result, 'result_bytes', len(json.dumps(result.rows, ensure_ascii=False, separators=(',', ':')).encode())),
            source_snapshot=getattr(result, 'source_snapshot', None))
    return values
