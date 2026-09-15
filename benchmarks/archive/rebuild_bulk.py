"""Prepared-block ClickHouse write floor; not a complete materializer.

Preparation uses the unchanged projection over verified local fixtures. Timed
inserts exclude projection/encoding/compression and operational claims. Both HTTP
variants send identical compact JSON into fresh disposable material databases.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
import zstandard

from dataset import describe, load_inventory, select
from rebuild_profile import FixtureStore, ProjectionOnly, storage
from periplus.ingestion.archive import Archive
from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig
from run import emit


def prepare(args):
    args.blocks.mkdir()
    captures = select(load_inventory(args.inventory), args.size, "unique")
    archive = Archive(FixtureStore(args.cache, captures))
    storage.write_claims = lambda identities: nullcontext()
    material = ProjectionOnly()
    pending = {table: bytearray() for table in ("captures", "html_documents")}
    counts = dict.fromkeys(pending, 0)
    fields, blocks = {}, []
    identities = {table: [] for table in pending}
    compressor = zstandard.ZstdCompressor(level=1)
    compression_cpu = 0.0

    def flush(table):
        nonlocal compression_cpu
        data = bytes(pending[table])
        if not data:
            return
        assert len(data) <= 32 * 1024 * 1024
        name = str(len(blocks))
        started = time.process_time()
        encoded = compressor.compress(data)
        compression_cpu += time.process_time() - started
        assert zstandard.ZstdDecompressor().decompress(encoded) == data
        (args.blocks / (name + ".jsonl")).write_bytes(data)
        (args.blocks / (name + ".zst")).write_bytes(encoded)
        blocks.append(
            dict(
                name=name,
                table=table,
                rows=counts[table],
                bytes=len(data),
                zstd_bytes=len(encoded),
                digest=sha256(data).hexdigest(),
            )
        )
        pending[table].clear()
        counts[table] = 0

    started = time.perf_counter()
    for index, capture in enumerate(captures):
        document, row = material.project(capture, archive, {})
        assert document is not None
        for table, value, identity in (
            ("html_documents", document, document["document_id"].upper()),
            ("captures", row, str(capture.capture_id)),
        ):
            fields.setdefault(table, list(value))
            data = (
                json.dumps(
                    value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                )
                + "\n"
            ).encode()
            if pending[table] and len(pending[table]) + len(data) > 8 * 1024 * 1024:
                flush(table)
            pending[table].extend(data)
            counts[table] += 1
            identities[table].append(
                dict(id=identity, digest=value["output_digest"].upper())
            )
        if (index + 1) % 250 == 0:
            emit(
                args.output,
                dict(
                    phase="prepare_progress",
                    captures=index + 1,
                    seconds=time.perf_counter() - started,
                ),
            )
    for table in pending:
        flush(table)
    tables = {
        table: dict(
            rows=len(rows),
            fingerprint=sha256(
                json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True).encode()
            ).hexdigest(),
        )
        for table, rows in identities.items()
    }
    manifest = dict(
        blocks=blocks, fields=fields, tables=tables, input=describe(captures)
    )
    (args.blocks / "manifest.json").write_text(json.dumps(manifest))
    emit(
        args.output,
        dict(
            phase="prepare_complete",
            tables=tables,
            input=manifest["input"],
            blocks=len(blocks),
            bytes=sum(b["bytes"] for b in blocks),
            zstd_bytes=sum(b["zstd_bytes"] for b in blocks),
            compression_cpu_seconds=compression_cpu,
            seconds=time.perf_counter() - started,
        ),
    )


def insert(args):
    manifest = json.loads((args.blocks / "manifest.json").read_text())
    config = ClickHouseConfig(
        url="http://periplus-archive-benchmark-db:8123",
        username="benchmark",
        password="disposable-benchmark",
    )
    admin = ClickHouseClient(config)
    try:
        for trial, encoding in enumerate(args.encodings):
            database = "material_" + uuid4().hex
            storage.install_material_schema(admin, database)
            statements = {}
            for table, fields in manifest["fields"].items():
                columns = {
                    r["name"]: r["type"]
                    for r in admin.query(f"DESCRIBE TABLE {database}.{table}")["data"]
                }
                structure = ", ".join(
                    f"{key} {columns[key].replace('FixedString(32)', 'String')}"
                    for key in fields
                ).replace("'", "''")
                selection = ", ".join(
                    f"unhex({key})" if "FixedString(32)" in columns[key] else key
                    for key in fields
                )
                statements[table] = (
                    f"INSERT INTO {database}.{table} ({', '.join(fields)}) "
                    f"SELECT {selection} FROM input('{structure}') FORMAT JSONEachRow"
                )
            prefix = "bulk_" + uuid4().hex

            def lane(blocks):
                sent = 0
                with httpx.Client(
                    base_url=config.url,
                    auth=(config.username, config.password.get_secret_value()),
                    timeout=httpx.Timeout(60, connect=5),
                    trust_env=False,
                    limits=httpx.Limits(max_connections=1),
                ) as client:
                    for block in blocks:
                        suffix = ".zst" if encoding == "zstd" else ".jsonl"
                        data = (args.blocks / (block["name"] + suffix)).read_bytes()
                        params = dict(
                            query=statements[block["table"]],
                            query_id=prefix + "_" + block["name"],
                            wait_end_of_query="1",
                            async_insert="0",
                            max_execution_time="45",
                            date_time_input_format="best_effort",
                        )
                        headers = (
                            {"Content-Encoding": "zstd"} if encoding == "zstd" else {}
                        )
                        response = client.post(
                            "/", params=params, headers=headers, content=data
                        )
                        if response.status_code != 200:
                            # Never expose server errors that might include source text.
                            raise RuntimeError(
                                f"Diagnostic insert HTTP {response.status_code}"
                            )
                        sent += len(data)
                return sent

            emit(
                args.output,
                dict(
                    phase="bulk_start",
                    database=database,
                    trial=trial,
                    encoding=encoding,
                    lanes=args.lanes,
                ),
            )
            try:
                started, cpu = time.perf_counter(), time.process_time()
                groups = [
                    manifest["blocks"][i :: args.lanes] for i in range(args.lanes)
                ]
                with ThreadPoolExecutor(max_workers=args.lanes) as pool:
                    sent = sum(pool.map(lane, groups))
                seconds = time.perf_counter() - started
                cpu_seconds = time.process_time() - cpu
                tables = {}
                for table, key in (
                    ("captures", "toString(capture_id)"),
                    ("html_documents", "hex(document_id)"),
                ):
                    rows = admin.query(
                        f"SELECT {key} AS id,hex(output_digest) AS digest "
                        f"FROM {database}.{table} ORDER BY id"
                    )["data"]
                    assert len({r["id"] for r in rows}) == len(rows)
                    tables[table] = dict(
                        rows=len(rows),
                        fingerprint=sha256(
                            json.dumps(rows, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                assert tables == manifest["tables"]
                parts = admin.query(
                    "SELECT sum(rows) AS rows,sum(bytes_on_disk) AS bytes,"
                    "sum(data_uncompressed_bytes) AS uncompressed_bytes,count() AS parts "
                    "FROM system.parts WHERE active AND database={database:String}",
                    parameters=dict(database=database),
                )["data"]
                admin.execute("SYSTEM FLUSH LOGS")
                queries = admin.query(
                    "SELECT count() AS queries,sum(query_duration_ms) AS duration_ms,"
                    "sum(ProfileEvents['UserTimeMicroseconds'] + "
                    "ProfileEvents['SystemTimeMicroseconds']) / 1e6 AS cpu_seconds,"
                    "max(memory_usage) AS max_query_memory "
                    "FROM system.query_log WHERE type='QueryFinish' "
                    "AND startsWith(query_id,{prefix:String})",
                    parameters=dict(prefix=prefix),
                )["data"]
                emit(
                    args.output,
                    dict(
                        phase="bulk_complete",
                        trial=trial,
                        encoding=encoding,
                        lanes=args.lanes,
                        seconds=seconds,
                        documents_per_second=tables["html_documents"]["rows"] / seconds,
                        client_cpu_seconds=cpu_seconds,
                        sent_bytes=sent,
                        tables=tables,
                        parts=parts,
                        insert_queries=queries,
                        note="Prepared-block insert floor; excludes projection, encoding, compression, claims, source reads and verification time; merges may remain",
                    ),
                )
            finally:
                admin.execute(f"DROP DATABASE {database} SYNC")
                emit(args.output, dict(phase="bulk_cleaned", database=database))
    finally:
        admin.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "insert"))
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument(
        "--encodings",
        nargs="+",
        choices=("plain", "zstd"),
        default=["plain", "zstd", "zstd", "plain"],
    )
    args = parser.parse_args()
    if args.phase == "prepare":
        if not args.inventory or not args.cache:
            parser.error("prepare requires --inventory and --cache")
        prepare(args)
    else:
        if args.lanes < 1:
            parser.error("lanes must be positive")
        insert(args)


if __name__ == "__main__":
    main()
