"""Create and resume a disposable, native ClickHouse access-path experiment."""

from __future__ import annotations

import argparse
import json

from client import ARTIFACTS, DATABASE, data, literal, require, target
from layouts import captures, documents, elements, jsonld
from load import SOURCE
from native_followups import (
    attribute_probe_ddl,
    json_name_ddl,
    keyword_capture_ddl,
    lean_elements_ddl,
    lean_full_elements_ddl,
    packed_documents_ddl,
)
from source import ANCHOR, capture_source_ddl, resume_captures, resume_documents, verify


def initialize(count: int) -> None:
    if not 2 <= count <= 20000:
        raise ValueError("Use between 2 and 20000 documents")
    if data(
        f"SELECT count() n FROM system.databases WHERE name={literal(DATABASE)}",
        workload="default",
    )[0]["n"]:
        raise RuntimeError(
            "Database exists; inspect it and use resume, never reinitialize in place"
        )
    require(
        f"CREATE WORKLOAD {DATABASE} IN all SETTINGS weight=1,max_concurrent_threads=2",
        read=False,
        workload="default",
    )
    require(f"CREATE DATABASE {DATABASE}", read=False)
    require(
        f"CREATE TABLE {target('source_ids')} (document_id FixedString(32),sample_rank UInt32) ENGINE=MergeTree ORDER BY document_id",
        read=False,
    )
    require(
        f"CREATE TABLE {target('source_docs')} AS {SOURCE}.html_documents", read=False
    )
    require(
        f"ALTER TABLE {target('source_docs')} ADD COLUMN sample_rank UInt32", read=False
    )
    require(capture_source_ddl(), read=False)
    require(
        f"INSERT INTO {target('source_ids')} SELECT document_id,row_number() OVER (ORDER BY document_id)+1 FROM (SELECT document_id FROM {SOURCE}.html_documents WHERE document_id!=unhex('{ANCHOR}') ORDER BY document_id LIMIT {count - 1})",
        read=False,
    )
    require(
        f"INSERT INTO {target('source_ids')} VALUES (unhex('{ANCHOR}'),1)", read=False
    )
    for name in ["docs_plain", "docs_indexed"]:
        require(documents(name, name == "docs_indexed"), read=False)
    for name in ["elements_plain", "elements_compact", "elements_full"]:
        require(
            elements(name, name == "elements_full", name != "elements_plain"),
            read=False,
        )
    for projection in ["none", "light", "cover"]:
        require(captures("captures_" + projection, projection), read=False)
    for name in ["json_plain", "json_indexed"]:
        require(jsonld(name, name == "json_indexed"), read=False)
    for factory in [
        attribute_probe_ddl,
        json_name_ddl,
        keyword_capture_ddl,
        lean_elements_ddl,
        lean_full_elements_ddl,
        packed_documents_ddl,
    ]:
        require(factory(), read=False)


def resume() -> None:
    resume_documents()
    resume_captures()
    result = verify()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "source-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


def cleanup() -> None:
    # Caller must first stop probe writers, then retain all measured local evidence.
    require(f"DROP DATABASE {DATABASE} SYNC", read=False, seconds=300)
    require(f"DROP WORKLOAD {DATABASE}", read=False, workload="default")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "resume", "verify", "cleanup"])
    parser.add_argument("--count", type=int, default=20000)
    args = parser.parse_args()
    if args.action == "init":
        initialize(args.count)
        resume()
    elif args.action == "resume":
        resume()
    elif args.action == "verify":
        print(json.dumps(verify()))
    else:
        cleanup()


if __name__ == "__main__":
    main()
