"""Run committed, Atlas-shaped DuckLake load through the DuckBasin minter."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

import duckdb
import httpx

from repository.catalogue.duckbasin import (
    DuckBasinClientMinter,
    MintedDuckDB,
)


@dataclass(frozen=True, slots=True)
class WorkloadConfig:
    writers: int
    batches_per_writer: int
    documents_per_batch: int
    elements_per_document: int
    interval_seconds: float
    compaction_wait_seconds: float

    @property
    def batch_count(self) -> int:
        return self.writers * self.batches_per_writer

    @property
    def document_count(self) -> int:
        return self.batch_count * self.documents_per_batch

    @property
    def element_count(self) -> int:
        return self.document_count * self.elements_per_document


@dataclass(slots=True)
class WriterResult:
    writer: int
    session_id: str = ""
    committed_batches: int = 0
    conflicts: int = 0
    retries: int = 0
    uploaded_bytes: int = 0
    latencies: list[float] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class ReaderResult:
    reader: int
    session_id: str = ""
    queries: int = 0
    latencies: list[float] = field(default_factory=list)
    error: str | None = None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Commit Atlas-shaped Parquet microbatches to a disposable Basin lake."
    )
    parser.add_argument("--writers", type=int, default=6)
    parser.add_argument("--batches-per-writer", type=int, default=10)
    parser.add_argument("--documents-per-batch", type=int, default=8)
    parser.add_argument("--elements-per-document", type=int, default=4_000)
    parser.add_argument("--interval-seconds", type=float, default=6)
    parser.add_argument("--readers", type=int, default=3)
    parser.add_argument("--compaction-wait-seconds", type=float, default=240)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=Path(".tmp/duckbasin-ducklake-load.csv"),
    )
    arguments = parser.parse_args()
    for name in (
        "writers",
        "batches_per_writer",
        "documents_per_batch",
        "elements_per_document",
        "readers",
    ):
        if getattr(arguments, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if arguments.interval_seconds < 0:
        parser.error("--interval-seconds cannot be negative")
    if arguments.compaction_wait_seconds <= 0:
        parser.error("--compaction-wait-seconds must be positive")
    return arguments


def remote_rows(minted: MintedDuckDB, sql: str) -> list[tuple]:
    return minted.connection.execute(
        "FROM quack_query_by_name(?, ?)",
        [minted.catalogue_alias, sql],
    ).fetchall()


def bootstrap(minter: DuckBasinClientMinter, schema: str) -> None:
    statements = (
        f"CREATE SCHEMA {_identifier(schema)}",
        f"""
        CREATE TABLE {_qualified(schema, "urls")} (
            url_id VARCHAR NOT NULL,
            normalized_url VARCHAR NOT NULL,
            scheme VARCHAR NOT NULL,
            host VARCHAR NOT NULL,
            port INTEGER NOT NULL,
            registrable_domain VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            query VARCHAR NOT NULL
        )
        """,
        f"""
        CREATE TABLE {_qualified(schema, "documents")} (
            document_id VARCHAR NOT NULL,
            html_sha256 VARCHAR NOT NULL,
            html_object_key VARCHAR NOT NULL,
            html_content_type VARCHAR NOT NULL,
            html_encoding VARCHAR NOT NULL,
            html_size_bytes BIGINT NOT NULL,
            html_compressed_size_bytes BIGINT NOT NULL,
            compression VARCHAR NOT NULL,
            dom_schema_version INTEGER NOT NULL,
            parser_name VARCHAR NOT NULL,
            parser_version VARCHAR NOT NULL,
            parser_options_hash VARCHAR NOT NULL,
            element_count BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
        f"""
        CREATE TABLE {_qualified(schema, "crawls")} (
            crawl_id UUID NOT NULL,
            document_id VARCHAR,
            graph_id UUID NOT NULL,
            graph_run_id UUID NOT NULL,
            graph_node_id UUID NOT NULL,
            crawl_request_id UUID NOT NULL,
            requested_url_id VARCHAR NOT NULL,
            final_url_id VARCHAR,
            captured_at TIMESTAMPTZ NOT NULL,
            status_code INTEGER,
            duration_ms BIGINT,
            response_media_type VARCHAR,
            policy_config_hash VARCHAR NOT NULL,
            policy_config_json JSON NOT NULL,
            outcome VARCHAR NOT NULL
        )
        """,
        f"""
        CREATE TABLE {_qualified(schema, "elements")} (
            document_id VARCHAR NOT NULL,
            element_index INTEGER NOT NULL,
            parent_index INTEGER,
            subtree_end_index INTEGER NOT NULL,
            depth INTEGER NOT NULL,
            tag VARCHAR NOT NULL,
            namespace_uri VARCHAR,
            attributes MAP(VARCHAR, VARCHAR) NOT NULL,
            text_direct VARCHAR NOT NULL,
            text_tail VARCHAR NOT NULL
        )
        """,
        f"ALTER TABLE {_qualified(schema, 'crawls')} SET PARTITIONED BY "
        "(year(captured_at), month(captured_at), day(captured_at))",
        f"ALTER TABLE {_qualified(schema, 'elements')} SET PARTITIONED BY "
        "(bucket(256, document_id))",
        f"ALTER TABLE {_qualified(schema, 'urls')} SET SORTED BY (url_id)",
        f"ALTER TABLE {_qualified(schema, 'documents')} SET SORTED BY (document_id)",
        f"ALTER TABLE {_qualified(schema, 'crawls')} "
        "SET SORTED BY (requested_url_id, captured_at)",
        f"ALTER TABLE {_qualified(schema, 'elements')} "
        "SET SORTED BY (document_id, element_index)",
    )
    with minter.mint() as minted:
        for statement in statements:
            remote_rows(minted, statement)


def generate_batch(
    local: duckdb.DuckDBPyConnection,
    directory: Path,
    *,
    run_id: str,
    writer: int,
    batch: int,
    documents: int,
    elements_per_document: int,
) -> dict[str, Path]:
    prefix = f"{run_id}_w{writer:02d}_b{batch:03d}"
    files = {
        table: directory / f"{prefix}_{table}.parquet"
        for table in ("urls", "documents", "crawls", "elements")
    }
    start = writer * 1_000_000 + batch * documents
    _copy_query(
        local,
        f"""
        SELECT
            '{prefix}_u_' || lpad(value::VARCHAR, 4, '0') AS url_id,
            'https://load.example/{prefix}/' || value AS normalized_url,
            'https' AS scheme,
            'load.example' AS host,
            443 AS port,
            'example' AS registrable_domain,
            '/{prefix}/' || value AS path,
            '' AS query
        FROM range({documents}) rows(value)
        """,
        files["urls"],
    )
    _copy_query(
        local,
        f"""
        SELECT
            '{prefix}_d_' || lpad(value::VARCHAR, 4, '0') AS document_id,
            md5('{prefix}_html_' || value) AS html_sha256,
            'load/{run_id}/html/' || md5('{prefix}_html_' || value) AS html_object_key,
            'text/html' AS html_content_type,
            'utf-8' AS html_encoding,
            262144 + value AS html_size_bytes,
            65536 + value AS html_compressed_size_bytes,
            'zstd' AS compression,
            1 AS dom_schema_version,
            'html5lib' AS parser_name,
            '1.1' AS parser_version,
            md5('parser-options-v1') AS parser_options_hash,
            {elements_per_document} AS element_count,
            current_timestamp + value * INTERVAL 1 MICROSECOND AS created_at
        FROM range({documents}) rows(value)
        """,
        files["documents"],
    )
    _copy_query(
        local,
        f"""
        SELECT
            CAST(md5('{prefix}_crawl_' || value) AS UUID) AS crawl_id,
            '{prefix}_d_' || lpad(value::VARCHAR, 4, '0') AS document_id,
            CAST(md5('graph-{run_id}') AS UUID) AS graph_id,
            CAST(md5('run-{run_id}') AS UUID) AS graph_run_id,
            CAST(md5('node-{run_id}') AS UUID) AS graph_node_id,
            CAST(md5('{prefix}_request_' || value) AS UUID) AS crawl_request_id,
            '{prefix}_u_' || lpad(value::VARCHAR, 4, '0') AS requested_url_id,
            '{prefix}_u_' || lpad(value::VARCHAR, 4, '0') AS final_url_id,
            current_timestamp + ({start} + value) * INTERVAL 1 MICROSECOND AS captured_at,
            200 AS status_code,
            100 + value AS duration_ms,
            'text/html' AS response_media_type,
            md5('policy-v1') AS policy_config_hash,
            '{{"version":1}}'::JSON AS policy_config_json,
            'succeeded' AS outcome
        FROM range({documents}) rows(value)
        """,
        files["crawls"],
    )
    element_rows = documents * elements_per_document
    _copy_query(
        local,
        f"""
        WITH generated AS (
            SELECT
                value,
                value // {elements_per_document} AS document_number,
                value % {elements_per_document} AS element_number
            FROM range({element_rows}) rows(value)
        )
        SELECT
            '{prefix}_d_' || lpad(document_number::VARCHAR, 4, '0') AS document_id,
            element_number::INTEGER AS element_index,
            CASE WHEN element_number = 0 THEN NULL
                 ELSE ((element_number - 1) // 4)::INTEGER END AS parent_index,
            element_number::INTEGER AS subtree_end_index,
            (element_number % 12)::INTEGER AS depth,
            ['html', 'body', 'main', 'section', 'div', 'p', 'a', 'span']
                [1 + (element_number % 8)] AS tag,
            CASE WHEN element_number % 17 = 0
                 THEN 'http://www.w3.org/2000/svg' ELSE NULL END AS namespace_uri,
            map(
                ['data-atlas-load'],
                [md5('{prefix}_attribute_' || value)]
            ) AS attributes,
            repeat(md5('{prefix}_text_' || value), 8) AS text_direct,
            '' AS text_tail
        FROM generated
        """,
        files["elements"],
    )
    return files


def _copy_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    path: Path,
) -> None:
    connection.execute(
        f"COPY ({query}) TO {_literal(str(path))} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 16384)"
    )


def commit_batch(
    minter: DuckBasinClientMinter,
    minted: MintedDuckDB,
    schema: str,
    files: dict[str, Path],
    *,
    prefix: str,
    expected_documents: int,
) -> tuple[MintedDuckDB, int, int]:
    retries = 0
    conflicts = 0
    while True:
        try:
            minted.connection.execute("BEGIN TRANSACTION")
            for table in ("urls", "documents", "elements", "crawls"):
                minted.connection.execute(
                    f"INSERT INTO {_catalogue_table(minted, schema, table)} BY NAME "
                    "SELECT * FROM read_parquet(?)",
                    [str(files[table])],
                )
            minted.connection.execute("COMMIT")
            return minted, retries, conflicts
        except BaseException as exc:
            message = str(exc).lower()
            if "conflict" in message or "transaction" in message:
                conflicts += 1
            try:
                minted.connection.execute("ROLLBACK")
            except BaseException:
                pass
            committed = _batch_document_count(minted, schema, prefix)
            if committed == expected_documents:
                return minted, retries, conflicts
            if committed != 0:
                raise RuntimeError(
                    f"partial transaction for {prefix}: {committed} documents"
                ) from exc
            retries += 1
            if retries >= 5:
                raise
            minted.close()
            time.sleep(min(2.0, 0.1 * 2**retries))
            minted = minter.mint()


def _batch_document_count(
    minted: MintedDuckDB,
    schema: str,
    prefix: str,
) -> int:
    rows = remote_rows(
        minted,
        f"SELECT count(*) FROM {_qualified(schema, 'documents')} "
        f"WHERE document_id LIKE {_literal(prefix + '_d_%')}",
    )
    return int(rows[0][0])


def writer_worker(
    minter: DuckBasinClientMinter,
    schema: str,
    run_id: str,
    writer: int,
    config: WorkloadConfig,
    cancel: threading.Event,
) -> WriterResult:
    result = WriterResult(writer=writer)
    minted: MintedDuckDB | None = None
    try:
        minted = minter.mint()
        result.session_id = minted.session_id
        with (
            tempfile.TemporaryDirectory(
                prefix=f"atlas-duckbasin-writer-{writer}-"
            ) as temp_dir,
            duckdb.connect(":memory:", config={"threads": "1"}) as local,
        ):
            directory = Path(temp_dir)
            for batch in range(config.batches_per_writer):
                if cancel.is_set():
                    break
                prefix = f"{run_id}_w{writer:02d}_b{batch:03d}"
                files = generate_batch(
                    local,
                    directory,
                    run_id=run_id,
                    writer=writer,
                    batch=batch,
                    documents=config.documents_per_batch,
                    elements_per_document=config.elements_per_document,
                )
                result.uploaded_bytes += sum(
                    path.stat().st_size for path in files.values()
                )
                started = time.monotonic()
                minted, retries, conflicts = commit_batch(
                    minter,
                    minted,
                    schema,
                    files,
                    prefix=prefix,
                    expected_documents=config.documents_per_batch,
                )
                result.latencies.append(time.monotonic() - started)
                result.retries += retries
                result.conflicts += conflicts
                result.committed_batches += 1
                for path in files.values():
                    path.unlink(missing_ok=True)
                if config.interval_seconds:
                    cancel.wait(config.interval_seconds)
    except BaseException as exc:
        result.error = f"{exc.__class__.__name__}: {exc}"
        cancel.set()
    finally:
        if minted is not None:
            minted.close()
    return result


def reader_worker(
    minter: DuckBasinClientMinter,
    schema: str,
    reader: int,
    stop: threading.Event,
    cancel: threading.Event,
) -> ReaderResult:
    result = ReaderResult(reader=reader)
    try:
        with minter.mint() as minted:
            result.session_id = minted.session_id
            queries = (
                f"SELECT count(*), coalesce(sum(element_count), 0) "
                f"FROM {_qualified(schema, 'documents')}",
                f"SELECT count(*), coalesce(sum(length(text_direct)), 0) "
                f"FROM {_qualified(schema, 'elements')}",
                f"SELECT tag, count(*) FROM {_qualified(schema, 'elements')} "
                "GROUP BY tag ORDER BY tag",
                f"SELECT count(*) FROM {_qualified(schema, 'crawls')} AS crawl "
                f"JOIN {_qualified(schema, 'documents')} AS document "
                "USING (document_id)",
            )
            while not stop.is_set() and not cancel.is_set():
                started = time.monotonic()
                remote_rows(minted, queries[result.queries % len(queries)])
                result.latencies.append(time.monotonic() - started)
                result.queries += 1
                stop.wait(0.25)
    except BaseException as exc:
        result.error = f"{exc.__class__.__name__}: {exc}"
        cancel.set()
    return result


def materialize(minter: DuckBasinClientMinter, schema: str) -> None:
    with minter.mint() as minted:
        remote_rows(
            minted,
            f"CREATE TABLE {_qualified(schema, 'tag_counts')} AS "
            f"SELECT tag, count(*) AS element_count "
            f"FROM {_qualified(schema, 'elements')} GROUP BY tag",
        )


def verify(
    minter: DuckBasinClientMinter,
    schema: str,
    config: WorkloadConfig,
) -> dict[str, int]:
    with minter.mint() as minted:
        rows = remote_rows(
            minted,
            f"""
            SELECT
                (SELECT count(*) FROM {_qualified(schema, "urls")}),
                (SELECT count(*) FROM {_qualified(schema, "documents")}),
                (SELECT count(*) FROM {_qualified(schema, "crawls")}),
                (SELECT count(*) FROM {_qualified(schema, "elements")}),
                (SELECT count(DISTINCT document_id)
                   FROM {_qualified(schema, "elements")}),
                (SELECT coalesce(sum(element_index), 0)
                   FROM {_qualified(schema, "elements")}),
                (SELECT coalesce(sum(element_count), 0)
                   FROM {_qualified(schema, "tag_counts")})
            """,
        )[0]
    actual = {
        "urls": int(rows[0]),
        "documents": int(rows[1]),
        "crawls": int(rows[2]),
        "elements": int(rows[3]),
        "element_documents": int(rows[4]),
        "element_index_sum": int(rows[5]),
        "materialized_elements": int(rows[6]),
    }
    expected_index_sum = (
        config.document_count
        * config.elements_per_document
        * (config.elements_per_document - 1)
        // 2
    )
    expected = {
        "urls": config.document_count,
        "documents": config.document_count,
        "crawls": config.document_count,
        "elements": config.element_count,
        "element_documents": config.document_count,
        "element_index_sum": expected_index_sum,
        "materialized_elements": config.element_count,
    }
    if actual != expected:
        raise RuntimeError(
            f"durability verification mismatch: expected={expected}, actual={actual}"
        )
    return actual


class BasinObserver:
    def __init__(self, minter: DuckBasinClientMinter) -> None:
        self.minter = minter
        self.client = httpx.Client(timeout=minter.config.request_timeout_seconds)
        self.lake_id = str(minter.target().lake_id)

    def close(self) -> None:
        self.client.close()

    def get(self, path: str) -> Any:
        token = self.minter.tokens.get()
        response = self.client.get(
            f"{self.minter.config.base_url}{path}",
            headers={"Authorization": f"Bearer {token.value}"},
        )
        if response.status_code == 401:
            self.minter.tokens.invalidate(token)
            token = self.minter.tokens.get()
            response = self.client.get(
                f"{self.minter.config.base_url}{path}",
                headers={"Authorization": f"Bearer {token.value}"},
            )
        response.raise_for_status()
        return response.json()

    def state(self) -> dict[str, Any]:
        lake = self.minter.lake_status()
        stats = self.get(f"/api/ducklakes/{self.lake_id}/stats/")
        compaction = self.get(
            f"/api/ducklakes/{self.lake_id}/compaction/"
        )
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "desired_replicas": lake["quack_desired_replicas"],
            "observed_replicas": lake["quack_observed_replicas"],
            "quack_status": lake["quack_status"],
            "parquet_file_count": stats["parquet_file_count"],
            "parquet_size_bytes": stats["parquet_size_bytes"],
            "compaction_status": compaction["status"],
            "dirty_table_count": compaction["dirty_table_count"],
            "last_decision": compaction["last_decision"],
            "last_error": compaction["last_error"],
            "recent_runs": compaction["recent_runs"],
        }


def monitor(
    observer: BasinObserver,
    output: Path,
    phase: list[str],
    stop: threading.Event,
    samples: list[dict[str, Any]],
) -> None:
    fields = (
        "timestamp",
        "phase",
        "desired_replicas",
        "observed_replicas",
        "quack_status",
        "parquet_file_count",
        "parquet_size_bytes",
        "compaction_status",
        "dirty_table_count",
        "last_decision",
        "last_error",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        while not stop.is_set():
            try:
                state = observer.state()
                state["phase"] = phase[0]
                samples.append(state)
                writer.writerow({key: state[key] for key in fields})
                stream.flush()
                print(
                    f"[{phase[0]}] replicas={state['observed_replicas']}/"
                    f"{state['desired_replicas']} "
                    f"files={state['parquet_file_count']} "
                    f"bytes={state['parquet_size_bytes']} "
                    f"compaction={state['compaction_status']} "
                    f"dirty={state['dirty_table_count']} "
                    f"decision={state['last_decision']}",
                    flush=True,
                )
            except Exception as exc:
                print(f"observer sample failed: {exc}", flush=True)
            stop.wait(5)


def successful_compaction(
    state: dict[str, Any],
    schema: str,
) -> dict[str, Any] | None:
    for run in state["recent_runs"]:
        if (
            run["schema_name"] == schema
            and run["status"] == "succeeded"
            and int(run["files_processed"]) > int(run["files_created"])
        ):
            return run
    return None


def cleanup(minter: DuckBasinClientMinter, schema: str) -> None:
    with minter.mint() as minted:
        remote_rows(
            minted,
            f"DROP SCHEMA {_identifier(schema)} CASCADE",
        )


def main() -> None:
    arguments = parse_arguments()
    config = WorkloadConfig(
        writers=arguments.writers,
        batches_per_writer=arguments.batches_per_writer,
        documents_per_batch=arguments.documents_per_batch,
        elements_per_document=arguments.elements_per_document,
        interval_seconds=arguments.interval_seconds,
        compaction_wait_seconds=arguments.compaction_wait_seconds,
    )
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    schema = f"load_{run_id}"
    print(
        f"run={run_id} schema={schema} writers={config.writers} "
        f"batches={config.batch_count} documents={config.document_count} "
        f"elements={config.element_count}",
        flush=True,
    )
    cancel = threading.Event()
    reader_stop = threading.Event()
    monitor_stop = threading.Event()
    phase = ["bootstrap"]
    writer_results: list[WriterResult] = []
    reader_results: list[ReaderResult] = []
    result_lock = threading.Lock()
    samples: list[dict[str, Any]] = []
    with DuckBasinClientMinter() as minter:
        observer = BasinObserver(minter)
        initial = observer.state()
        if int(initial["parquet_file_count"]) != 0:
            print(
                "warning: the lake was not empty before this run; "
                "global file deltas include pre-existing data",
                flush=True,
            )
        bootstrap(minter, schema)
        monitor_thread = threading.Thread(
            target=monitor,
            args=(
                observer,
                arguments.metrics_file,
                phase,
                monitor_stop,
                samples,
            ),
            daemon=True,
        )
        monitor_thread.start()

        def read_target(reader: int) -> None:
            result = reader_worker(
                minter,
                schema,
                reader,
                reader_stop,
                cancel,
            )
            with result_lock:
                reader_results.append(result)

        reader_threads = [
            threading.Thread(
                target=read_target,
                args=(reader,),
                daemon=True,
            )
            for reader in range(arguments.readers)
        ]
        for thread in reader_threads:
            thread.start()

        def write_target(writer: int) -> None:
            result = writer_worker(
                minter,
                schema,
                run_id,
                writer,
                config,
                cancel,
            )
            with result_lock:
                writer_results.append(result)

        phase[0] = "ingestion"
        writer_threads = [
            threading.Thread(
                target=write_target,
                args=(writer,),
                daemon=True,
            )
            for writer in range(config.writers)
        ]
        started = time.monotonic()
        for thread in writer_threads:
            thread.start()
        for thread in writer_threads:
            thread.join()
        if cancel.is_set():
            reader_stop.set()
            for thread in reader_threads:
                thread.join()
            monitor_stop.set()
            monitor_thread.join(timeout=10)
            observer.close()
            errors = [
                result.error
                for result in writer_results + reader_results
                if result.error
            ]
            raise SystemExit(f"workload failed: {errors}")

        phase[0] = "materialization"
        materialize(minter, schema)
        before_compaction = verify(minter, schema, config)
        state_before_wait = observer.state()
        print(
            "ingestion complete: "
            f"files={state_before_wait['parquet_file_count']} "
            f"bytes={state_before_wait['parquet_size_bytes']}",
            flush=True,
        )

        phase[0] = "compaction"
        deadline = time.monotonic() + config.compaction_wait_seconds
        compacted: dict[str, Any] | None = None
        while time.monotonic() < deadline and not cancel.is_set():
            state = observer.state()
            compacted = successful_compaction(state, schema)
            if compacted is not None:
                break
            time.sleep(5)

        phase[0] = "remount-verification"
        reader_stop.set()
        for thread in reader_threads:
            thread.join()
        after_compaction = verify(minter, schema, config)
        final = observer.state()
        monitor_stop.set()
        monitor_thread.join(timeout=10)
        observer.close()

        errors = [
            result.error
            for result in writer_results + reader_results
            if result.error
        ]
        elapsed = time.monotonic() - started
        print("\nDuckLake load result")
        print(f"elapsed_seconds={elapsed:.1f}")
        print(
            f"committed_batches="
            f"{sum(result.committed_batches for result in writer_results)}"
        )
        print(
            f"staged_parquet_bytes="
            f"{sum(result.uploaded_bytes for result in writer_results)}"
        )
        print(
            f"transaction_retries="
            f"{sum(result.retries for result in writer_results)}"
        )
        print(
            f"transaction_conflicts="
            f"{sum(result.conflicts for result in writer_results)}"
        )
        print(
            f"reader_queries={sum(result.queries for result in reader_results)}"
        )
        print(f"errors={len(errors)}")
        print(f"before_compaction={before_compaction}")
        print(f"after_compaction={after_compaction}")
        print(
            "active_files="
            f"{state_before_wait['parquet_file_count']}->{final['parquet_file_count']}"
        )
        print(
            "active_bytes="
            f"{state_before_wait['parquet_size_bytes']}->{final['parquet_size_bytes']}"
        )
        print(f"compaction_run={compacted}")
        print(
            "replica_range="
            f"{min(int(sample['observed_replicas']) for sample in samples)}-"
            f"{max(int(sample['observed_replicas']) for sample in samples)}"
        )
        print(f"metrics_file={arguments.metrics_file.resolve()}")
        if errors:
            raise SystemExit(f"worker errors: {errors}")
        if compacted is None:
            raise SystemExit("automatic compaction did not complete in time")
        if not arguments.keep:
            cleanup(minter, schema)
            print(f"cleaned_schema={schema}")


def _catalogue_table(
    minted: MintedDuckDB,
    schema: str,
    table: str,
) -> str:
    return ".".join(
        (
            _identifier(minted.catalogue_alias),
            _identifier(schema),
            _identifier(table),
        )
    )


def _qualified(schema: str, table: str) -> str:
    return f"{_identifier(schema)}.{_identifier(table)}"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    main()
