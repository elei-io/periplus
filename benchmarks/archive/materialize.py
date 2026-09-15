"""Actual MaterialStore/PG-claim/ClickHouse proof with a layout byte-reader adapter.

Only the raw byte reader is replaced. Parsing, output bounds, digests, insert
verification and PostgreSQL claims run unchanged. NATS delivery and publication
are outside this storage comparison. Local measurements are not homelab timings.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values
import psycopg

from dataset import load_inventory, select, describe
from io_store import MeteredStore, cleanup
from layouts import Separate, Packed
from periplus.ingestion.objects.store import FileObjectStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000)
    args = parser.parse_args()
    config = dotenv_values(args.env)
    token = uuid4().hex
    pg_database = "archive_benchmark_" + token
    connection = dict(
        host="127.0.0.1",
        port=55432,
        user=config.get("PERIPLUS_POSTGRES_USER") or "periplus",
        password=config.get("PERIPLUS_POSTGRES_PASSWORD") or "periplus_local",
    )
    from psycopg import sql

    with psycopg.connect(**connection, dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(pg_database)))
    from sqlalchemy import URL

    os.environ["PERIPLUS_CONTROL_DATABASE_URL"] = URL.create(
        "postgresql+psycopg",
        username=connection["user"],
        password=connection["password"],
        host="127.0.0.1",
        port=55432,
        database=pg_database,
    ).render_as_string(hide_password=False)
    from periplus.platform.postgres.session import get_engine
    from periplus.retention.models import WriteClaimRecord

    WriteClaimRecord.__table__.create(get_engine())
    from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig
    from periplus.materialization.storage import MaterialStore, install_material_schema

    client = ClickHouseClient(
        ClickHouseConfig(
            url="http://127.0.0.1:8123",
            username=config.get("PERIPLUS_CLICKHOUSE_USER") or "periplus",
            password=config.get("PERIPLUS_CLICKHOUSE_PASSWORD") or "clickhouse_local",
        )
    )
    captures = select(load_inventory(args.inventory), args.size)
    loader = lambda c: (args.cache / c.payload.content_id).read_bytes()
    reports, expected = [], None
    root = args.output.parent / ("material-" + token)
    try:
        for kind in ("separate", "cas", "warc"):
            store = MeteredStore(FileObjectStore(root / kind))
            layout = Separate(store) if kind == "separate" else Packed(store, kind)
            layout.ingest(captures, loader)
            layout.recover()
            database = "material_" + uuid4().hex
            install_material_schema(client, database)
            material = MaterialStore(client, database)
            by_key = {c.payload.object_key: c for c in captures}

            class Reader:
                def __init__(self, unused):
                    pass

                def iter_bytes(self, key):
                    yield layout.body(by_key[key])

            archive = SimpleNamespace(store=store, retired=lambda identity: False)
            failures = []
            store.reset()
            start = time.perf_counter()
            try:
                with patch(
                    "periplus.materialization.storage.RawHtmlRepository", Reader
                ):
                    for capture in captures:
                        try:
                            material.materialize_many([capture], archive)
                        except Exception as exc:
                            # Keep URLs and source bytes out of the benchmark report.
                            cause = exc.__cause__ or exc
                            failures.append(
                                dict(
                                    id=str(capture.capture_id),
                                    error=type(cause).__name__,
                                    message=str(cause)[:160],
                                )
                            )
                seconds = time.perf_counter() - start
                digest_sets = {}
                for table, key in [
                    ("captures", "toString(capture_id)"),
                    ("html_documents", "hex(document_id)"),
                ]:
                    rows = client.query(
                        f"SELECT {key} AS id, hex(output_digest) AS digest "
                        f"FROM {database}.{table} ORDER BY id"
                    )["data"]
                    digest_sets[table] = dict(
                        rows=len(rows),
                        fingerprint=sha256(
                            json.dumps(rows, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                proof = dict(digests=digest_sets, failures=failures)
                if expected is None:
                    expected = proof
                elif proof != expected:
                    raise ValueError("Material output or failures differ by layout")
                report = dict(
                    kind=kind,
                    seconds=seconds,
                    input=describe(captures),
                    object_io=store.stats(),
                    **proof,
                )
                reports.append(report)
                args.output.write_text(json.dumps(reports, indent=2) + "\n")
                print(json.dumps(report), flush=True)
            finally:
                client.execute(f"DROP DATABASE {database} SYNC")
                cleanup(store)
    finally:
        client.close()
        get_engine().dispose()
        with psycopg.connect(**connection, dbname="postgres", autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(pg_database)
                )
            )


if __name__ == "__main__":
    main()
