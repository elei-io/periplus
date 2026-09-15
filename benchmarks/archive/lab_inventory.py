"""Read-only legacy inventory; run inside an existing legacy Periplus pod.

Writes gzip JSON lines to stdout. No source bodies, controls or metadata are changed.
The exported rows are benchmark inputs, not an adoption/retention decision.
"""

import gzip
import json
import sys


def main() -> None:
    from periplus.platform.catalogue.config import catalogue_config_from_env
    from periplus.platform.catalogue.connection import DuckLakeConnectionFactory

    config = catalogue_config_from_env()
    connection = DuckLakeConnectionFactory(
        config,
        duckdb_config={
            "threads": "1",
            "memory_limit": "1GB",
            "max_temp_directory_size": "2GB",
        },
    ).connect(read_only=True)
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(f"USE {config.alias}")
        cursor = connection.execute("""
            SELECT v.visit_id::VARCHAR AS capture_id, v.requested_url,
                   v.effective_url, v.observed_at, v.status_code,
                   d.representation, d.declared_media_type, d.detected_media_type,
                   d.charset, d.content_sha256, d.content_bytes, d.object_key,
                   d.storage_encoding, d.stored_bytes
            FROM ingest.visits v JOIN ingest.documents d ON d.document_id=v.document_id
            ORDER BY v.visit_id
        """)
        names = [column[0] for column in cursor.description]
        with gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb", mtime=0) as output:
            while rows := cursor.fetchmany(1024):
                for row in rows:
                    output.write(
                        (json.dumps(dict(zip(names, row)), default=str) + "\n").encode()
                    )
        connection.execute("COMMIT")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
