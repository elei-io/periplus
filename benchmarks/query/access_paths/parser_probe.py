"""Read-only raw-body fetch + existing Lexbor materialization CPU measurement.

Run in the disposable Periplus image, one document at a time. It writes no archive
objects and does not pretend to measure acknowledgements, captures or retirement.
"""

import json
import os
import time
from hashlib import sha256

import httpx
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.html import RawHtmlRepository, html_object_key
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.html_content import html_content
from periplus.materialization.recipe import recipe_digest
from periplus.materialization.storage import output_row
from worker_probe import DATABASE


def run() -> None:
    with httpx.Client(
        base_url=os.environ["PERIPLUS_CLICKHOUSE_URL"],
        auth=(
            os.environ["PERIPLUS_CLICKHOUSE_USER"],
            os.environ["PERIPLUS_CLICKHOUSE_PASSWORD"],
        ),
        trust_env=False,
        timeout=60,
    ) as client:
        response = client.post(
            "/",
            params={"workload": DATABASE, "max_threads": "1"},
            content=f"SELECT lower(hex(content_id)) AS content_id,lower(hex(document_id)) AS document_id,sample_rank,hex(SHA256(document_text)) AS text_digest,length(elements) AS nodes FROM {DATABASE}.source_docs WHERE sample_rank<=100 OR sample_rank=162 ORDER BY sample_rank FORMAT JSON",
        )
        response.raise_for_status()
        inputs = response.json()["data"]
    raw = RawHtmlRepository(object_store_from_env(maximum_concurrency=1))
    print(json.dumps({"recipe": recipe_digest(), "documents": len(inputs)}), flush=True)
    for item in inputs:
        begin = time.monotonic()
        source = raw.read_bytes(html_object_key(item["content_id"]))
        fetch = time.monotonic() - begin
        begin = time.monotonic()
        cpu = time.process_time()
        nodes, elements = parse_document(source.decode("utf-8"))
        parsed = html_content(item["content_id"], nodes, elements)
        parse_cpu = time.process_time() - cpu
        parse_wall = time.monotonic() - begin
        assert len(parsed["elements"]) == int(item["nodes"])
        assert (
            sha256(parsed["document_text"].encode()).hexdigest().upper()
            == item["text_digest"]
        )
        begin = time.process_time()
        parsed.pop("content_sha256")
        encoded = output_row(
            {
                "document_id": item["document_id"],
                "content_id": item["content_id"],
                "representation": "rendered_html",
                "encoding": "utf-8",
                **parsed,
            }
        )
        encode_cpu = time.process_time() - begin
        print(
            json.dumps(
                {
                    "rank": item["sample_rank"],
                    "raw_bytes": len(source),
                    "nodes": len(elements),
                    "fetch_verify_s": fetch,
                    "parse_cpu_s": parse_cpu,
                    "parse_wall_s": parse_wall,
                    "nested_encode_cpu_s": encode_cpu,
                    "nested_wire_bytes": len(encoded.wire),
                    "verified": True,
                }
            ),
            flush=True,
        )
        del source, nodes, elements, parsed, encoded


if __name__ == "__main__":
    run()
