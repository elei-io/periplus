"""Measure complete large-document writes/reuse in a disposable ClickHouse target.

Input is a JSON array of Capture contracts. Raw objects are only read. Run this
in a pod with the same memory limit as a worker; each capture uses a fresh child
process so peak RSS is attributable. The generated database is always dropped.
"""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
from uuid import uuid4

from periplus.ingestion.archive import Archive
from periplus.ingestion.captures import Capture
from periplus.ingestion.objects.config import object_store_from_env
from periplus.materialization import storage
from periplus.platform.clickhouse import connect_clickhouse


def measure(capture: Capture, database: str) -> None:
    archive = Archive(object_store_from_env())
    # This isolated target has no publication or competing writers. Never claim
    # live captures or write their raw journal just to measure a projection.
    storage.write_claims = lambda identities: nullcontext()
    archive.location = lambda capture: "raw/corpus/v1/benchmark/isolated#0"
    client = connect_clickhouse()
    material = storage.MaterialStore(client, database)
    started = time.monotonic()
    document, row = material.project(capture, archive, {})
    projected = time.monotonic()
    assert document is not None
    size = len(storage.canonical(document))
    elements = len(document["elements"])
    material._insert_verified("html_documents", {document["document_id"]: document})
    material._insert_verified("captures", {str(capture.capture_id): row})
    inserted = time.monotonic()
    digest = document["output_digest"]
    del document, row
    # Exercise the existing-document lookup used by repeated captures as well as
    # exact retry verification. No giant response is retained alongside parsing.
    document, row = material.project(capture, archive, {})
    assert document["output_digest"] == digest
    assert len(document["elements"]) == elements
    material._insert_verified("html_documents", {document["document_id"]: document})
    material._insert_verified("captures", {str(capture.capture_id): row})
    assert material.complete(capture)
    print(json.dumps(dict(capture_id=str(capture.capture_id), input_bytes=capture.payload.byte_length,
        output_bytes=size, elements=elements, projection_seconds=projected-started,
        insert_seconds=inserted-projected, reuse_retry_seconds=time.monotonic()-inserted,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)), flush=True)
    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--child")
    parser.add_argument("--database")
    parser.add_argument("--batch", action="store_true")
    args = parser.parse_args()
    if args.child:
        measure(Capture.model_validate_json(args.child), storage.material_database(args.database))
        return
    captures = [Capture.model_validate(row) for row in json.loads(args.captures.read_text())]
    if args.batch:
        archive = Archive(object_store_from_env())
        storage.write_claims = lambda identities: nullcontext()
        archive.location = lambda capture: "raw/corpus/v1/benchmark/isolated#0"
        archive.retired = lambda identity: False
        client = connect_clickhouse()
        material = storage.MaterialStore(client, storage.material_database(args.database))
        started = time.monotonic()
        assert material.materialize_many(captures, archive) == len(captures)
        assert material.materialize_many(captures, archive) == len(captures)
        print(json.dumps(dict(batch_captures=len(captures), seconds=time.monotonic()-started,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)), flush=True)
        client.close()
        return
    database = "material_" + uuid4().hex
    client = connect_clickhouse()
    client.execute(f"CREATE DATABASE {database}")
    failures = 0
    try:
        storage.install_material_schema(client, database)
        for capture in captures:
            print(json.dumps(dict(start=str(capture.capture_id), database=database)), flush=True)
            result = subprocess.run([sys.executable, __file__, "--child", capture.model_dump_json(), "--database", database], timeout=300)
            failures += result.returncode != 0
            print(json.dumps(dict(capture_id=str(capture.capture_id), exit=result.returncode)), flush=True)
        for table in ("captures", "html_documents"):
            client.execute(f"TRUNCATE TABLE {database}.{table}")
        result = subprocess.run([sys.executable, __file__, "--batch", "--captures", str(args.captures), "--database", database], timeout=300)
        failures += result.returncode != 0
    finally:
        client.execute(f"DROP DATABASE {database} SYNC")
        client.close()
    if failures:
        raise SystemExit(f"{failures} captures failed")


if __name__ == "__main__":
    main()
