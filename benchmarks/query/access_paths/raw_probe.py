"""One-core, four-fetch raw-to-element pilot. No live queues or publication."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import uuid4

import httpx
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.html import RawHtmlRepository, html_object_key
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.html_content import html_content
from worker_probe import DATABASE, FIELDS, encode


def run() -> None:
    settings = {
        "workload": DATABASE,
        "max_threads": "1",
        "max_memory_usage": "2147483648",
        "max_execution_time": "60",
        "output_format_json_quote_64bit_integers": "0",
    }
    config = {
        "base_url": os.environ["PERIPLUS_CLICKHOUSE_URL"],
        "auth": (
            os.environ["PERIPLUS_CLICKHOUSE_USER"],
            os.environ["PERIPLUS_CLICKHOUSE_PASSWORD"],
        ),
        "trust_env": False,
        "timeout": 90,
    }
    raw = RawHtmlRepository(object_store_from_env(maximum_concurrency=4))
    with httpx.Client(**config) as client:
        response = client.post(
            "/",
            params=settings,
            content=f"SELECT lower(hex(content_id)) AS content_id,lower(hex(document_id)) AS document_id,sample_rank,hex(SHA256(document_text)) AS text_digest,length(elements) AS nodes FROM {DATABASE}.source_docs WHERE sample_rank<=100 ORDER BY sample_rank FORMAT JSON",
        )
        response.raise_for_status()
        inputs = response.json()["data"]
        empty = client.post(
            "/",
            params=settings,
            content=f"SELECT count() FROM {DATABASE}.elements_raw_probe",
        )
        empty.raise_for_status()
        if int(empty.text.strip()):
            raise RuntimeError("Pilot destination is not empty")
        started = time.monotonic()
        cpu_started = time.process_time()
        buffer = bytearray()
        rows = wire_bytes = 0

        def flush() -> None:
            nonlocal buffer
            if not buffer:
                return
            identity = "access-" + uuid4().hex
            before = time.monotonic()
            sql = f"INSERT INTO {DATABASE}.elements_raw_probe (document_id,sample_rank,{','.join(FIELDS)},text) FORMAT RowBinary"
            response = client.post(
                "/",
                params={**settings, "query_id": identity, "query": sql},
                content=bytes(buffer),
            )
            print(
                json.dumps(
                    {
                        "label": "raw_insert",
                        "query_id": identity,
                        "bytes": len(buffer),
                        "wall_s": time.monotonic() - before,
                        "status": response.status_code,
                    }
                ),
                flush=True,
            )
            if response.status_code != 200:
                raise RuntimeError(response.text[:1500])
            buffer = bytearray()

        def fetch(item: dict) -> bytes:
            return raw.read_bytes(html_object_key(item["content_id"]))

        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {i: pool.submit(fetch, item) for i, item in enumerate(inputs[:4])}
            for i, item in enumerate(inputs):
                body = pending.pop(i).result()
                if i + 4 < len(inputs):
                    pending[i + 4] = pool.submit(fetch, inputs[i + 4])
                nodes, elements = parse_document(body.decode("utf-8"))
                parsed = html_content(item["content_id"], nodes, elements)
                assert len(parsed["elements"]) == item["nodes"]
                assert (
                    sha256(parsed["document_text"].encode()).hexdigest().upper()
                    == item["text_digest"]
                )
                document = {**item, **parsed}
                for element in parsed["elements"]:
                    row = encode(document, element)
                    if len(buffer) + len(row) > 96 * 1024 * 1024:
                        flush()
                    buffer.extend(row)
                    rows += 1
                    wire_bytes += len(row)
                del body, nodes, elements, parsed, document
        flush()
        print(
            json.dumps(
                {
                    "label": "raw_total",
                    "documents": len(inputs),
                    "elements": rows,
                    "wire_bytes": wire_bytes,
                    "cpu_s": time.process_time() - cpu_started,
                    "wall_s": time.monotonic() - started,
                    "fetch_concurrency": 4,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    run()
