"""Separate local material capacity: real parser, PG claims and ClickHouse writes.

No archive load runs here. Input files are verified fixture copies; there is no
patched parser or object reader. NATS/planner/publication overhead is excluded.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
from functools import partial
from types import SimpleNamespace
import time
from uuid import uuid4

from dotenv import dotenv_values
import psycopg
from psycopg import sql

from dataset import load_inventory, select, describe
from io_store import MeteredStore, cleanup
from periplus.ingestion.archive import Archive
from periplus.ingestion.objects.store import FileObjectStore
from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig
from run import emit, source_store

material = None
archive = None


def initialize(config, database, root, s3_reads, compact_json):
    from periplus.materialization.storage import MaterialStore
    from periplus.platform.postgres.session import get_engine

    assert get_engine().url.database.startswith("archive_benchmark_")
    if compact_json:
        # Explicit software control, scoped to this worker's client module.
        # Production row admission measures compact JSON; its current transport
        # adds spaces and can push an admitted row above the HTTP byte limit.
        import periplus.platform.clickhouse.client as client_module

        client_module.json = SimpleNamespace(
            loads=json.loads, dumps=partial(json.dumps, separators=(",", ":"))
        )
    global material, archive
    material = MaterialStore(ClickHouseClient(ClickHouseConfig(**config)), database)
    archive = Archive(
        MeteredStore(source_store(8) if s3_reads else FileObjectStore(Path(root)))
    )


def execute(captures):
    from periplus.materialization.storage import MaterialInputError

    start, cpu = time.perf_counter(), time.process_time()
    archive.store.reset()
    failures = []
    initial_failure = None
    try:
        material.materialize_many(captures, archive)
    except MaterialInputError as initial:
        cause = initial.__cause__ or initial
        initial_failure = dict(
            error=type(cause).__name__,
            message=str(cause)[:200],
            code=getattr(cause, "code", None),
        )
        # Diagnostic continuation to account for every input. Production blocks
        # publication on the failed batch; this fallback is not worker behavior.
        for capture in captures:
            try:
                material.materialize_many([capture], archive)
            except MaterialInputError as exc:
                cause = exc.__cause__ or exc
                failures.append(
                    dict(
                        id=str(capture.capture_id),
                        error=type(cause).__name__,
                        message=str(cause)[:160],
                    )
                )
    return dict(
        count=len(captures),
        failures=failures,
        initial_failure=initial_failure,
        seconds=time.perf_counter() - start,
        cpu_seconds=time.process_time() - cpu,
        object_io=archive.store.stats(),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path)
    parser.add_argument(
        "--lab",
        action="store_true",
        help="Disposable lab databases and real source S3 reads",
    )
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--compact-json", action="store_true")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    args = parser.parse_args()
    config = dotenv_values(args.env) if args.env else {}
    token = uuid4().hex
    pg_database = "archive_benchmark_" + token
    connection = dict(
        host="127.0.0.1",
        port=55432,
        user=config.get("PERIPLUS_POSTGRES_USER") or "periplus",
        password=config.get("PERIPLUS_POSTGRES_PASSWORD") or "periplus_local",
    )
    if args.lab:
        connection = dict(
            host="periplus-archive-benchmark-db",
            port=5432,
            user="benchmark",
            password="disposable-benchmark",
        )
    with psycopg.connect(**connection, dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(pg_database)))
    from sqlalchemy import URL

    os.environ["PERIPLUS_CONTROL_DATABASE_URL"] = URL.create(
        "postgresql+psycopg",
        username=connection["user"],
        password=connection["password"],
        host=connection["host"],
        port=connection["port"],
        database=pg_database,
    ).render_as_string(hide_password=False)
    from periplus.platform.postgres.session import get_engine
    from periplus.retention.models import WriteClaimRecord

    assert get_engine().url.database == pg_database
    from periplus.materialization.storage import install_material_schema

    WriteClaimRecord.__table__.create(get_engine())
    ch_config = dict(
        url="http://127.0.0.1:8123",
        username=config.get("PERIPLUS_CLICKHOUSE_USER") or "periplus",
        password=config.get("PERIPLUS_CLICKHOUSE_PASSWORD") or "clickhouse_local",
    )
    if args.lab:
        ch_config = dict(
            url="http://periplus-archive-benchmark-db:8123",
            username="benchmark",
            password="disposable-benchmark",
        )
    client = ClickHouseClient(ClickHouseConfig(**ch_config))
    captures = select(load_inventory(args.inventory), args.size, "unique")
    root = args.output.parent / ("material-capacity-" + token)
    store = MeteredStore(FileObjectStore(root))
    expected = None
    try:
        for capture in captures:
            path = root / capture.payload.object_key
            path.parent.mkdir(parents=True, exist_ok=True)
            # Copy, never mutate or delete fixture-cache source files.
            path.write_bytes((args.cache / capture.payload.content_id).read_bytes())
            Archive(store).verify_payload(capture)
        batches, batch, size = [], [], 0
        for capture in captures:
            if batch and (
                len(batch) >= 32
                or size + capture.payload.byte_length > 32 * 1024 * 1024
            ):
                batches.append(batch)
                batch, size = [], 0
            batch.append(capture)
            size += capture.payload.byte_length
        if batch:
            batches.append(batch)
        for workers in args.workers:
            # Independent capacity trials, not recovery retries. The previous
            # pool has exited and its ClickHouse target was dropped synchronously.
            # Reset only this UUID-isolated database's fixture claims.
            with get_engine().begin() as claim_connection:
                claim_connection.execute(WriteClaimRecord.__table__.delete())
            database = "material_" + uuid4().hex
            install_material_schema(client, database)
            emit(
                args.output,
                dict(
                    phase="material_start",
                    compact_json=args.compact_json,
                    workers=workers,
                    database=database,
                    pg_database=pg_database,
                    input=describe(captures),
                ),
            )
            results = []
            start = time.perf_counter()
            try:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=initialize,
                    initargs=(
                        ch_config,
                        database,
                        str(root),
                        args.lab,
                        args.compact_json,
                    ),
                ) as pool:
                    for future in as_completed(
                        [pool.submit(execute, b) for b in batches]
                    ):
                        results.append(future.result())
                        if len(results) % 10 == 0:
                            emit(
                                args.output,
                                dict(
                                    phase="material_progress",
                                    workers=workers,
                                    seconds=time.perf_counter() - start,
                                    captures=sum(r["count"] for r in results),
                                ),
                            )
                seconds = time.perf_counter() - start
                failures = sorted(
                    [f for r in results for f in r["failures"]], key=lambda f: f["id"]
                )
                tables = {}
                for table, key in [
                    ("captures", "toString(capture_id)"),
                    ("html_documents", "hex(document_id)"),
                ]:
                    rows = client.query(
                        f"SELECT {key} AS id,hex(output_digest) AS digest FROM {database}.{table} ORDER BY id"
                    )["data"]
                    assert len({r["id"] for r in rows}) == len(rows)
                    tables[table] = dict(
                        rows=len(rows),
                        fingerprint=sha256(
                            json.dumps(rows, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                assert tables["captures"]["rows"] + len(failures) == len(captures)
                proof = dict(tables=tables, failures=failures)
                if expected is not None:
                    assert proof == expected
                expected = proof
                emit(
                    args.output,
                    dict(
                        phase="material_complete",
                        compact_json=args.compact_json,
                        workers=workers,
                        seconds=seconds,
                        captures_per_second=tables["captures"]["rows"] / seconds,
                        cpu_seconds=sum(r["cpu_seconds"] for r in results),
                        batch_results=results,
                        **proof,
                    ),
                )
            finally:
                client.execute(f"DROP DATABASE {database} SYNC")
    finally:
        client.close()
        get_engine().dispose()
        with psycopg.connect(**connection, dbname="postgres", autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(pg_database)
                )
            )
        cleanup(store)
        emit(args.output, dict(phase="material_cleaned", pg_database=pg_database))


if __name__ == "__main__":
    main()
