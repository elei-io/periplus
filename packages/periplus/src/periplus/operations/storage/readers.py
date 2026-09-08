"""Bounded reads of existing lake, database, and object-store metadata."""
from pathlib import Path
import threading
import time

import psycopg
from sqlalchemy import text

from periplus.platform.catalogue.config import CatalogueConfig
from periplus.ingestion.objects.store import ObjectStore
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory, _identifier
from periplus.operations.storage.models import (
    DatabaseTable, Evidence, Footprint, LakeTable, Retention, RetirementStage,
)

TABLE_LIMIT = 200
OBJECT_LIMIT = 10_000


def lake(config: CatalogueConfig) -> tuple[Evidence, list[LakeTable], bool, Retention]:
    connection = DuckLakeConnectionFactory(config, duckdb_config={
        "threads": "1", "memory_limit": "512MB", "max_temp_directory_size": "256MB",
    }).connect(read_only=True)
    timer = threading.Timer(15, connection.interrupt)
    timer.start()
    try:
        connection.execute(f"USE {_identifier(config.alias)}")
        connection.execute("BEGIN TRANSACTION")
        observations = connection.execute("SELECT count(*) FROM ingest.visits").fetchone()[0]
        row = connection.execute("""
            SELECT count(*), count(DISTINCT visit_id), coalesce(sum(stored_bytes), 0),
                   median(stored_bytes), quantile_cont(stored_bytes, 0.95)
            FROM ingest.documents
        """).fetchone()
        unique = connection.execute("""
            SELECT count(*), coalesce(sum(bytes), 0) FROM (
                SELECT object_key, max(stored_bytes) AS bytes
                FROM ingest.documents WHERE object_key IS NOT NULL GROUP BY object_key
            )
        """).fetchone()
        evidence = Evidence(observations=observations, documents=row[0],
                            observations_with_documents=row[1], referenced_bytes=row[2],
                            median_bytes=row[3], p95_bytes=row[4],
                            unique_objects=unique[0], unique_bytes=unique[1])
        relations = connection.execute("""
            SELECT schema_name, table_name, estimated_size FROM duckdb_tables()
            WHERE database_name = current_database()
            ORDER BY schema_name, table_name LIMIT ?
        """, [TABLE_LIMIT + 1]).fetchall()
        tables = []
        for schema, name, rows in relations[:TABLE_LIMIT]:
            files, data_bytes, delete_files, delete_bytes = connection.execute("""
                SELECT count(*), coalesce(sum(data_file_size_bytes), 0),
                       count(delete_file), coalesce(sum(delete_file_size_bytes), 0)
                FROM ducklake_list_files(?, ?, schema := ?)
            """, [config.alias, name, schema]).fetchone()
            generation = "rebuilding" if name.startswith("_periplus_rebuild_") else "retired" if name.startswith("_periplus_retired_") else "current"
            role = "evidence" if schema == "ingest" else "projection" if schema == "material" and (not name.startswith("_periplus_") or generation != "current") else "bookkeeping" if name.startswith("_periplus_") else "other"
            tables.append(LakeTable(schema_name=schema, name=name, role=role,
                                   generation=generation, estimated_rows=rows,
                                   bytes=data_bytes + delete_bytes, data_bytes=data_bytes,
                                   delete_bytes=delete_bytes, files=files, delete_files=delete_files))
        # Snapshot/grace state is authoritative; API environment settings need not match the janitor.
        stages = connection.execute("""
            SELECT CASE WHEN retired_snapshot < 0 THEN 'boundary'
                        WHEN snapshots_cleared_at IS NULL THEN 'snapshots'
                        ELSE 'grace' END AS stage,
                   count(*), coalesce(sum(stored_bytes), 0), min(retired_at)
            FROM material._periplus_retention_objects GROUP BY stage
        """).fetchall()
        stage_rows = {row[0]: row[1:] for row in stages}
        receipts = connection.execute("""
            SELECT count(*) FILTER (WHERE kind='observation'),
                   count(*) FILTER (WHERE kind='collection'), max(retired_at)
            FROM material._periplus_retention_identities WHERE retired_at IS NOT NULL
        """).fetchone()
        snapshots = connection.execute(f"SELECT count(*), min(snapshot_time) FROM {_identifier(config.alias)}.snapshots()").fetchone()
        retention = Retention(
            stages=[RetirementStage(id=key, name=label,
                                   objects=stage_rows.get(key, (0, 0, None))[0],
                                   expected_bytes=stage_rows.get(key, (0, 0, None))[1],
                                   oldest_at=stage_rows.get(key, (0, 0, None))[2])
                    for key, label in [("boundary", "Establishing snapshot boundary"),
                                       ("snapshots", "Awaiting snapshot clearance"),
                                       ("grace", "Reader grace / awaiting reclaimer")]],
            retired_observations=receipts[0], retired_requests=receipts[1],
            latest_retirement_at=receipts[2], snapshots=snapshots[0], oldest_snapshot_at=snapshots[1],
        )
        return evidence, tables, len(relations) <= TABLE_LIMIT, retention
    finally:
        timer.cancel()
        timer.join()
        connection.close()


def control_database(sessions):
    with sessions() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '5s'"))
        size = session.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        rows = session.execute(text("""
            SELECT schemaname || '.' || relname, pg_table_size(relid),
                   pg_indexes_size(relid), pg_total_relation_size(relid), greatest(n_live_tup, 0)
            FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 200
        """)).all()
        return Footprint(id="control", name="Control Postgres", bytes=size, complete=True,
                         basis="Database files; excludes WAL, backups and replicas."), [
            DatabaseTable(name=row[0], table_bytes=row[1], index_bytes=row[2], total_bytes=row[3], estimated_rows=row[4]) for row in rows
        ]


def lake_metadata(config: CatalogueConfig) -> Footprint:
    if config.metadata_path.startswith("postgres:"):
        with psycopg.connect(config.metadata_path[len("postgres:"):], connect_timeout=3,
                             options="-c statement_timeout=5000 -c default_transaction_read_only=on") as connection:
            size = connection.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        return Footprint(id="metadata", name="DuckLake metadata", bytes=size, complete=True,
                         basis="Metadata Postgres database, including inlined data; excludes WAL and replicas.")
    if "://" not in config.metadata_path:
        return Footprint(id="metadata", name="DuckLake metadata", bytes=Path(config.metadata_path).stat().st_size,
                         complete=True, basis="Local metadata database file; excludes WAL and backups.")
    return Footprint(id="metadata", name="DuckLake metadata", basis="Metadata database", reason="This metadata storage protocol has no size reader.")


def inventory(store: ObjectStore, *, prefixes: tuple[str, ...], id: str, name: str) -> Footprint:
    """A bounded metadata inventory; incomplete scans never claim complete coverage."""
    started = time.monotonic()
    count = size = 0
    for prefix in prefixes:
        for item in store.list_objects(prefix):
            if count >= OBJECT_LIMIT or time.monotonic() - started > 5:
                return Footprint(id=id, name=name, bytes=size, complete=False,
                                 basis="Object metadata inventory", reason=f"Partial inventory: scanned {count:,} objects; 10,000-object / 5-second scan budget.")
            size += item.size
            count += 1
    return Footprint(id=id, name=name, bytes=size, complete=True,
                     basis=f"Object metadata inventory · {count:,} {'object' if count == 1 else 'objects'} · not a transactional snapshot.")
