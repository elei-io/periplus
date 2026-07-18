"""Shared orchestration and failure diagnostics for Atlas reliability smokes."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import duckdb


ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
TERMINAL_RUN_STATES = {
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
}
T = TypeVar("T")
_QUACK_CONNECTION: duckdb.DuckDBPyConnection | None = None
_QUACK_RUNTIME: dict[str, Any] | None = None


@dataclass(frozen=True)
class MaterializationFixture:
    created: bool
    materialization_id: str
    view_reference_id: str | None = None
    ducklake_table_uuid: str | None = None


def api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
    return json.loads(body) if body else None


def api_text(path: str) -> str:
    with urlopen(f"{API}{path}", timeout=10) as response:
        return response.read().decode(errors="replace")


def materialization_lag(run_id: str) -> dict[str, Any]:
    connection, runtime = _catalogue_quack_connection()
    definitions = [
        {
            "materialization_id": item["id"],
            "definition_revision_id": item["definition_revision_id"],
            "scope_kind": item["scope_kind"],
        }
        for item in api("GET", "/catalogue/materializations/")["items"]
        if item["source_state"] == "current"
        and item["live_enabled"]
        and item["dematerialization_requested_at"] is None
    ]
    if not definitions:
        return {
            "run_id": run_id,
            "materialization_count": 0,
            "pending_updates": 0,
            "failed_updates": 0,
        }

    active_values = ", ".join(
        (
            f"({_quote_literal(item['materialization_id'])}, "
            f"{_quote_literal(item['definition_revision_id'])}, "
            f"{_quote_literal(item['scope_kind'])})"
        )
        for item in definitions
    )
    crawls = _quote_qualified(
        runtime["catalogue_alias"],
        runtime["catalogue_schema"],
        "crawls",
    )
    coverage = _quote_qualified(
        runtime["catalogue_alias"],
        "_atlas",
        "materialization_coverage",
    )
    rows = _quack_rows(
        connection,
        f"""
        WITH active(materialization_id, definition_revision_id, scope_kind) AS (
            VALUES {active_values}
        ),
        expected_scopes AS (
            SELECT DISTINCT a.materialization_id,
                   a.definition_revision_id,
                   a.scope_kind,
                   CASE WHEN a.scope_kind = 'crawl'
                        THEN CAST(c.crawl_id AS VARCHAR)
                        ELSE c.document_id
                   END AS scope_id
            FROM {crawls} AS c
            CROSS JOIN active AS a
            WHERE c.graph_run_id = {_quote_literal(run_id)}
              AND (a.scope_kind = 'crawl' OR c.document_id IS NOT NULL)
        )
        SELECT count(DISTINCT e.materialization_id),
               count(*) FILTER (WHERE r.status IS NULL),
               count(*) FILTER (WHERE r.status = 'failed')
        FROM expected_scopes AS e
        LEFT JOIN {coverage} AS r
          ON r.materialization_id = e.materialization_id
         AND r.definition_revision_id = e.definition_revision_id
         AND r.scope_kind = e.scope_kind
         AND r.scope_id = e.scope_id
        """,
    )
    row = rows[0]
    return {
        "run_id": run_id,
        "materialization_count": int(row[0]),
        "pending_updates": int(row[1]),
        "failed_updates": int(row[2]),
    }


def _catalogue_quack_connection() -> tuple[duckdb.DuckDBPyConnection, dict[str, Any]]:
    global _QUACK_CONNECTION, _QUACK_RUNTIME
    if _QUACK_CONNECTION is not None and _QUACK_RUNTIME is not None:
        return _QUACK_CONNECTION, _QUACK_RUNTIME

    runtime = api("GET", "/catalogue/query-runtime")
    connection = duckdb.connect()
    try:
        connection.install_extension("quack")
        connection.load_extension("quack")
        connection.execute(
            "ATTACH "
            f"{_quote_literal(runtime['quack_uri'])} AS _atlas_quack "
            f"(TYPE quack, TOKEN {_quote_literal(runtime['quack_token'])})"
        )
        alias = runtime["catalogue_alias"]
        exists = bool(
            _quack_rows(
                connection,
                (
                    "SELECT database_name FROM duckdb_databases() "
                    f"WHERE database_name = {_quote_literal(alias)}"
                ),
            )
        )
        if not exists:
            for statement in runtime["setup_sql"]:
                _quack_rows(connection, statement)
            try:
                _quack_rows(connection, runtime["attach_sql"])
            except duckdb.Error:
                exists = bool(
                    _quack_rows(
                        connection,
                        (
                            "SELECT database_name FROM duckdb_databases() "
                            f"WHERE database_name = {_quote_literal(alias)}"
                        ),
                    )
                )
                if not exists:
                    raise
        _quack_rows(
            connection,
            (
                f"USE {_quote_identifier(alias)}."
                f"{_quote_identifier(runtime['catalogue_schema'])}"
            ),
        )
    except BaseException:
        connection.close()
        raise
    _QUACK_CONNECTION = connection
    _QUACK_RUNTIME = runtime
    return connection, runtime


def _quack_rows(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
) -> list[tuple[Any, ...]]:
    return connection.execute(
        "FROM quack_query_by_name('_atlas_quack', ?)",
        [sql],
    ).fetchall()


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_qualified(*values: str) -> str:
    return ".".join(_quote_identifier(value) for value in values)


def compose(*arguments: str, capture: bool = False) -> str:
    completed = subprocess.run(
        ["docker", "compose", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=capture,
        text=True,
    )
    return completed.stdout if capture else ""


def containers(service: str) -> list[str]:
    return [
        value
        for value in compose("ps", "-q", service, capture=True).splitlines()
        if value
    ]


def container_health(service: str) -> list[str]:
    statuses: list[str] = []
    for container_id in containers(service):
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container_id,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        statuses.append(completed.stdout.strip())
    return statuses


def require_healthy(service: str, *, expected: int | None = None) -> None:
    statuses = container_health(service)
    if expected is not None and len(statuses) != expected:
        raise RuntimeError(
            f"{service} has {len(statuses)} replicas, expected {expected}: {statuses}"
        )
    if not statuses or any(status != "healthy" for status in statuses):
        raise RuntimeError(f"{service} is not healthy: {statuses or ['missing']}")


def wait_until(
    operation: Callable[[], T | None],
    *,
    timeout: float,
    description: str,
    interval: float = 0.25,
) -> T:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = operation()
            if result is not None:
                return result
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    suffix = f"; last error: {last_error}" if last_error is not None else ""
    raise RuntimeError(f"timed out waiting for {description}{suffix}")


def wait_for_run(run_id: str, *, timeout: float = 180) -> dict[str, Any]:
    def settled() -> dict[str, Any] | None:
        run = api("GET", f"/graph-runs/{run_id}")
        return run if run["status"] in TERMINAL_RUN_STATES else None

    return wait_until(
        settled,
        timeout=timeout,
        description=f"graph run {run_id} to settle",
        interval=1,
    )


def ensure_active_materialization(token: str) -> MaterializationFixture:
    existing = api("GET", "/catalogue/materializations/")["items"]
    if existing:
        return MaterializationFixture(
            created=False,
            materialization_id=existing[0]["id"],
        )
    slug = f"reliability_{token[:12].lower()}"
    view = api(
        "POST",
        "/catalogue/views/",
        {
            "slug": slug,
            "description": "Disposable deployment reliability fixture",
            "sql": "SELECT crawl_id, requested_url FROM crawls",
        },
    )
    try:
        materialization = api(
            "PUT",
            f"/catalogue/views/{view['id']}/materialization",
            {
                "name": slug,
                "display_name": f"Reliability {token[:8]}",
                "description": "Disposable deployment reliability fixture",
                "scope_kind": "crawl",
                "scope_column": "crawl_id",
                "backfill_scopes_per_minute": 10000,
                "partition_column": None,
            },
        )
    except BaseException:
        api(
            "DELETE",
            (
                f"/catalogue/views/{view['id']}/object"
                f"?expected_ducklake_view_uuid={view['ducklake_view_uuid']}"
            ),
        )
        raise
    return MaterializationFixture(
        created=True,
        materialization_id=materialization["id"],
        view_reference_id=view["id"],
        ducklake_table_uuid=materialization["ducklake_table_uuid"],
    )


def cleanup_materialization_fixture(
    fixture: MaterializationFixture, *, timeout: float = 90
) -> None:
    if not fixture.created:
        return
    if fixture.view_reference_id is None or fixture.ducklake_table_uuid is None:
        raise RuntimeError("created materialization fixture is incomplete")
    api(
        "DELETE",
        (
            f"/catalogue/materializations/{fixture.materialization_id}"
            f"?expected_ducklake_table_uuid={fixture.ducklake_table_uuid}"
        ),
    )

    def dematerialized() -> bool | None:
        items = api("GET", "/catalogue/materializations/")["items"]
        return True if all(item["id"] != fixture.materialization_id for item in items) else None

    wait_until(
        dematerialized,
        timeout=timeout,
        description=f"materialization {fixture.materialization_id} removal",
        interval=1,
    )
    view = api("GET", f"/catalogue/views/{fixture.view_reference_id}")
    api(
        "DELETE",
        (
            f"/catalogue/views/{fixture.view_reference_id}/object"
            f"?expected_ducklake_view_uuid={view['ducklake_view_uuid']}"
        ),
    )


def diagnostic_directory(scenario: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / ".atlas" / "reliability" / f"{stamp}-{scenario}"
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def capture_diagnostics(scenario: str) -> Path:
    destination = diagnostic_directory(scenario)
    commands = {
        "compose-ps.txt": ["docker", "compose", "ps", "--all"],
        "compose-logs.txt": [
            "docker",
            "compose",
            "logs",
            "--no-color",
            "--timestamps",
            "--tail",
            "500",
        ],
    }
    for filename, command in commands.items():
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        (destination / filename).write_text(
            completed.stdout + completed.stderr,
            encoding="utf-8",
        )
    endpoints = {
        "capacity.json": "/graph-runs/capacity",
        "repository-dead-letters.json": "/operations/repository/dead-letters?limit=100",
        "materialization-dead-letters.json": (
            "/operations/repository/materialization-dead-letters?limit=100"
        ),
    }
    for filename, path in endpoints.items():
        try:
            value = api("GET", path)
            content = json.dumps(value, indent=2, sort_keys=True)
        except Exception as exc:
            content = f"{type(exc).__name__}: {exc}\n"
        (destination / filename).write_text(content, encoding="utf-8")
    try:
        metrics = api_text("/metrics")
    except Exception as exc:
        metrics = f"{type(exc).__name__}: {exc}\n"
    (destination / "api-metrics.txt").write_text(metrics, encoding="utf-8")
    worker_endpoints = {
        "atlas-acquisition-worker": (9099, 9090),
        "atlas-ingestion-worker": (9092, 9091),
        "atlas-materialization-worker": (9096, 9093),
        "atlas-maintenance-worker": (9095, 9094),
    }
    for service, (health_port, metrics_port) in worker_endpoints.items():
        try:
            service_containers = containers(service)
        except Exception as exc:
            (destination / f"{service}-discovery.txt").write_text(
                f"{type(exc).__name__}: {exc}\n",
                encoding="utf-8",
            )
            continue
        for index, container_id in enumerate(service_containers, start=1):
            for label, port, path in (
                ("health", health_port, "/healthz"),
                ("metrics", metrics_port, "/metrics"),
            ):
                command = [
                    "docker",
                    "exec",
                    container_id,
                    "python",
                    "-c",
                    (
                        "import urllib.request;"
                        f"print(urllib.request.urlopen('http://127.0.0.1:{port}{path}',"
                        "timeout=2).read().decode())"
                    ),
                ]
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                (destination / f"{service}-{index}-{label}.txt").write_text(
                    completed.stdout + completed.stderr,
                    encoding="utf-8",
                )
    return destination
