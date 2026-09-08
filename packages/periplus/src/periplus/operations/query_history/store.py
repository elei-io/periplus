"""Postgres owns short-lived private query history; reads aggregate in the database."""
from datetime import UTC, datetime, timedelta
from uuid import UUID
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from periplus.operations.query_history.models import QueryExecution
from periplus.operations.query_history.schemas import Dashboard, Execution, ExecutionPage

RETENTION_DAYS = 30
FILTER = """started_at >= :since AND started_at <= :until AND operation = :operation
AND (:source = 'all' OR source = :source OR
    (:source = 'public' AND source IN ('public_console', 'assistant', 'sdk')))
AND (CAST(:pattern AS text) IS NULL OR coalesce(query_fingerprint, 'unparsed') = :pattern)"""
STATS = """count(*) AS executions,
count(*) FILTER (WHERE outcome = 'success') AS successes,
count(*) FILTER (WHERE outcome <> 'success') AS failures,
count(*) FILTER (WHERE truncated IS TRUE) AS truncated,
percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_ms) FILTER (WHERE outcome = 'success') AS p50_ms,
percentile_cont(0.95) WITHIN GROUP (ORDER BY elapsed_ms) FILTER (WHERE outcome = 'success') AS p95_ms,
coalesce(sum(elapsed_ms), 0) AS total_ms"""

class QueryHistoryStore:
    def __init__(self, sessions):
        self.sessions = sessions

    def record(self, value: Execution):
        now = datetime.now(UTC)
        if value.started_at < now - timedelta(days=RETENTION_DAYS) or value.finished_at > now + timedelta(minutes=5):
            raise ValueError("Execution is outside the retention window")
        with self.sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '1s'"))
            # Idempotent delivery, never overwrite a terminal execution.
            session.execute(insert(QueryExecution).values(**value.model_dump()).on_conflict_do_nothing(
                index_elements=[QueryExecution.execution_id]))

    def cleanup(self, *, now=None, limit=5000):
        cutoff = (now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)
        with self.sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            expired = select(QueryExecution.execution_id).where(QueryExecution.started_at < cutoff).order_by(
                QueryExecution.started_at).limit(limit).with_for_update(skip_locked=True)
            return session.execute(delete(QueryExecution).where(QueryExecution.execution_id.in_(expired))).rowcount

    def dashboard(self, *, days=7, source="public", operation="execute", pattern=None, sort="executions", offset=0):
        now = datetime.now(UTC)
        params = dict(since=now - timedelta(days=days), until=now, source=source, operation=operation, pattern=pattern, offset=offset)
        order = {"executions": "executions", "p95": "p95_ms", "failures": "failures", "total": "total_ms"}[sort]
        with self.sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            def rows(sql):
                return [dict(row) for row in session.execute(text(sql), params).mappings()]
            base = f"FROM query_executions WHERE {FILTER}"
            summary = rows(f"SELECT {STATS} {base}")[0]
            trend = rows(f"SELECT date_trunc('day', started_at AT TIME ZONE 'UTC') AS day, {STATS} {base} GROUP BY 1 ORDER BY 1")
            patterns = rows(f"""SELECT coalesce(query_fingerprint, 'unparsed') AS pattern_key,
                min(query_template) AS query_template, max(started_at) AS last_seen,
                array_agg(DISTINCT source) AS sources, {STATS} {base}
                GROUP BY 1 ORDER BY {order} DESC NULLS LAST, pattern_key LIMIT 50 OFFSET :offset""")
            plans = rows(f"""SELECT plan_fingerprint, min(started_at) AS first_seen,
                max(started_at) AS last_seen,
                (array_agg(execution_id ORDER BY started_at DESC, execution_id DESC))[1] AS example_execution_id,
                duckdb_version, compiler_version,
                count(*) FILTER (WHERE outcome = 'timeout') AS timeouts, {STATS} {base}
                GROUP BY plan_fingerprint, duckdb_version, compiler_version
                ORDER BY last_seen DESC LIMIT 50""") if pattern is not None else []
            count = rows(f"SELECT count(DISTINCT coalesce(query_fingerprint, 'unparsed')) AS count {base}")[0]['count']
            failures = rows(f"SELECT coalesce(error_code, outcome) AS name, count(*) AS count {base} AND outcome <> 'success' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20")
            def usage(column):
                return rows(f"""SELECT value AS name, count(*) AS count FROM
                    (SELECT {column} FROM query_executions WHERE {FILTER}) q,
                    LATERAL jsonb_array_elements_text(q.{column}) value
                    GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20""")
            return Dashboard(summary=summary, trend=trend, patterns=patterns, plans=plans, pattern_count=count,
                             failures=failures, relations=usage('relations'), functions=usage('functions'))

    def executions(self, *, days=7, source="public", operation="execute", pattern=None, offset=0):
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = list(session.execute(text(f"""SELECT execution_id, started_at, source, outcome, error_code,
                elapsed_ms, result_rows, truncated FROM query_executions WHERE {FILTER}
                ORDER BY started_at DESC, execution_id DESC LIMIT 51 OFFSET :offset"""),
                dict(since=now-timedelta(days=days), until=now, source=source, operation=operation, pattern=pattern, offset=offset)).mappings())
            return ExecutionPage(executions=[dict(r) for r in rows[:50]], has_more=len(rows)>50)

    def detail(self, identity: UUID):
        with self.sessions() as session:
            row = session.scalar(select(QueryExecution).where(QueryExecution.execution_id == identity,
                QueryExecution.started_at >= datetime.now(UTC)-timedelta(days=RETENTION_DAYS)))
            return Execution.model_validate(row) if row else None
