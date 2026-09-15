"""One-time frozen lake export. Execute with the deployed old software, read-only."""

import gzip
import json
import sys


def main() -> None:
    from periplus.platform.catalogue.config import catalogue_config_from_env
    from periplus.platform.catalogue.connection import DuckLakeConnectionFactory

    config = catalogue_config_from_env()
    connection = DuckLakeConnectionFactory(
        config, duckdb_config={"threads": "1", "memory_limit": "1GB"}
    ).connect(read_only=True)
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(f"USE {config.alias}")
        snapshot = connection.execute(
            f"SELECT max(snapshot_id) FROM ducklake_snapshots('{config.alias}')"
        ).fetchone()[0]
        count = connection.execute("SELECT count(*) FROM ingest.visits").fetchone()[0]
        duplicates = connection.execute("""
            SELECT count(*) FROM (
                SELECT visit_id FROM ingest.visits GROUP BY visit_id HAVING count(*)<>1
            )
        """).fetchone()[0]
        if duplicates:
            raise ValueError("Duplicate source visit identities")
        cursor = connection.execute("""
            SELECT v.visit_id::VARCHAR AS capture_id, v.requested_url,
                   v.effective_url, v.observed_at, v.status_code, v.outcome,
                   v.document_id::VARCHAR AS visit_document_id,
                   d.document_id::VARCHAR AS document_id,
                   d.representation, d.declared_media_type, d.detected_media_type,
                   d.charset, d.content_sha256, d.content_bytes, d.object_key,
                   d.storage_encoding, d.stored_bytes
            FROM ingest.visits v LEFT JOIN ingest.documents d
                ON d.document_id=v.document_id
            ORDER BY v.visit_id
        """)
        names = [column[0] for column in cursor.description]
        emitted = 0
        with gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb", mtime=0) as output:
            output.write((json.dumps({"snapshot": snapshot, "visits": count})+"\n").encode())
            while rows := cursor.fetchmany(1024):
                for values in rows:
                    row = dict(zip(names, values))
                    if row["visit_document_id"] and not row["document_id"]:
                        raise ValueError("Source visit references a missing document")
                    output.write((json.dumps(row, default=str)+"\n").encode())
                    emitted += 1
            if emitted != count:
                raise ValueError("Source join changed visit cardinality")
        connection.execute("COMMIT")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
