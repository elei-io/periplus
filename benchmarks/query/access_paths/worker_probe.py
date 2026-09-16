"""Bounded full-text projection writer; runs in a disposable Periplus image pod.

Receives LOW/HIGH as positional arguments. Reads retained derived documents, not
raw HTML: this measures span expansion, encoding and insertion, not HTML parsing.
Uses ordinary ClickHouse RowBinary inserts and no query API transformation.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import time
from uuid import uuid4

import httpx

DATABASE = os.environ.get("PERIPLUS_BENCH_DATABASE", "bench_access_20260916")
if not re.fullmatch(r"bench_access_[a-z0-9_]+", DATABASE):
    raise ValueError("Use an isolated benchmark database")
FIELDS = [
    "node_index",
    "parent_index",
    "subtree_end_index",
    "sibling_index",
    "depth",
    "tag",
    "namespace",
    "attributes",
    "text_direct",
    "text_start",
    "text_end",
]


def varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return varint(len(encoded)) + encoded


def encode(document: dict, element: dict) -> bytes:
    out = bytearray(document["document_id"].encode("ascii"))
    out.extend(struct.pack("<II", document["sample_rank"], element["node_index"]))
    parent = element["parent_index"]
    out.extend(b"\x01" if parent is None else b"\x00" + struct.pack("<I", parent))
    out.extend(
        struct.pack(
            "<III",
            element["subtree_end_index"],
            element["sibling_index"],
            element["depth"],
        )
    )
    out.extend(string(element["tag"]))
    namespace = element["namespace"]
    out.extend(b"\x01" if namespace is None else b"\x00" + string(namespace))
    attributes = element["attributes"]
    out.extend(varint(len(attributes)))
    for key, value in attributes.items():
        out.extend(string(key))
        out.extend(string(value))
    out.extend(string(element["text_direct"]))
    out.extend(struct.pack("<QQ", element["text_start"], element["text_end"]))
    out.extend(
        string(document["document_text"][element["text_start"] : element["text_end"]])
    )
    return bytes(out)


def json_rows(response: httpx.Response):
    """Only LF delimits JSONEachRow; Unicode line separators are page content."""
    pending = bytearray()
    for chunk in response.iter_bytes():
        pending.extend(chunk)
        while (end := pending.find(b"\n")) >= 0:
            line = bytes(pending[:end])
            del pending[: end + 1]
            if line:
                yield json.loads(line)
    if pending:
        yield json.loads(pending)


def run(low: int, high: int, table: str = "elements_full", batch_mib: int = 32) -> None:
    if table not in {"elements_full", "elements_full_lean"}:
        raise ValueError("Invalid target")
    if batch_mib not in {32, 96}:
        raise ValueError("Invalid batch size")
    settings = {
        "workload": DATABASE,
        "max_threads": "1",
        "max_memory_usage": "2147483648",
        "max_execution_time": "600",
        "use_query_cache": "0",
        "use_query_condition_cache": "0",
        "output_format_json_quote_64bit_integers": "0",
        "output_format_json_named_tuples_as_objects": "1",
    }
    config = {
        "base_url": os.environ["PERIPLUS_CLICKHOUSE_URL"],
        "auth": (
            os.environ["PERIPLUS_CLICKHOUSE_USER"],
            os.environ["PERIPLUS_CLICKHOUSE_PASSWORD"],
        ),
        "timeout": httpx.Timeout(630, connect=5),
        "trust_env": False,
    }
    started = time.monotonic()
    cpu_started = time.process_time()
    buffer = bytearray()
    rows = documents = wire_bytes = 0
    encode_cpu = 0.0
    write_wall = 0.0
    with httpx.Client(**config) as reader, httpx.Client(**config) as writer:
        check = writer.post(
            "/",
            params=settings,
            content=f"SELECT count() FROM {DATABASE}.{table} WHERE sample_rank>{low} AND sample_rank<={high}",
        )
        check.raise_for_status()
        if int(check.text.strip()):
            raise RuntimeError(
                "Target range is not empty; inspect partial insert before replay"
            )

        def flush() -> None:
            nonlocal buffer, write_wall
            if not buffer:
                return
            query_id = "access-" + uuid4().hex
            sql = f"INSERT INTO {DATABASE}.{table} (document_id,sample_rank,{','.join(FIELDS)},text) FORMAT RowBinary"
            before = time.monotonic()
            response = writer.post(
                "/",
                params={**settings, "query_id": query_id, "query": sql},
                content=bytes(buffer),
            )
            elapsed = time.monotonic() - before
            write_wall += elapsed
            print(
                json.dumps(
                    {
                        "label": f"worker_insert:{table}:{low}:{high}",
                        "query_id": query_id,
                        "bytes": len(buffer),
                        "wall_s": elapsed,
                        "status": response.status_code,
                    }
                ),
                flush=True,
            )
            if response.status_code != 200:
                raise RuntimeError(response.text[:1500])
            buffer = bytearray()

        for start in range(low, high, 200):
            sql = f"SELECT lower(hex(document_id)) AS document_id,sample_rank,document_text,elements FROM {DATABASE}.source_docs WHERE sample_rank>{start} AND sample_rank<={min(start + 200, high)} SETTINGS max_block_size=1 FORMAT JSONEachRow"
            reader_id = "access-" + uuid4().hex
            print(
                json.dumps(
                    {
                        "label": f"worker_read:{table}:{start}:{min(start + 200, high)}",
                        "query_id": reader_id,
                    }
                ),
                flush=True,
            )
            with reader.stream(
                "POST",
                "/",
                params={**settings, "query_id": reader_id},
                content=sql,
            ) as response:
                response.raise_for_status()
                for doc in json_rows(response):
                    documents += 1
                    for element in doc["elements"]:
                        before = time.process_time()
                        row = encode(doc, element)
                        encode_cpu += time.process_time() - before
                        if len(buffer) + len(row) > batch_mib * 1024 * 1024:
                            flush()
                        buffer.extend(row)
                        wire_bytes += len(row)
                        rows += 1
        flush()
    print(
        json.dumps(
            {
                "label": f"worker_total:{table}:{low}:{high}",
                "documents": documents,
                "rows": rows,
                "wire_bytes": wire_bytes,
                "encode_cpu_s": encode_cpu,
                "cpu_s": time.process_time() - cpu_started,
                "write_wall_s": write_wall,
                "wall_s": time.monotonic() - started,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    run(
        int(sys.argv[1]),
        int(sys.argv[2]),
        sys.argv[3] if len(sys.argv) > 3 else "elements_full",
        int(sys.argv[4]) if len(sys.argv) > 4 else 32,
    )
