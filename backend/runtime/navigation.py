"""Ephemeral, reproducible navigation packages for graph execution."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

import pyarrow as pa
from config import get_int
from dom import PARSER_NAME, PARSER_OPTIONS_HASH, PARSER_VERSION, links_from_html
from repository.objects.store import ObjectStore
from runtime.navigation_contract import NavigationPackage

NAVIGATION_RECIPE = sha256(
    f"{PARSER_NAME}:{PARSER_VERSION}:{PARSER_OPTIONS_HASH}:links-v1".encode()
).hexdigest()
_EVENT_NAMESPACE = UUID("f0d15d8a-a735-48b7-a576-a08f85ecac74")

LINKS_SCHEMA = pa.schema(
    [
        ("document_id", pa.string()),
        ("url", pa.string()),
        ("text", pa.string()),
        ("title", pa.string()),
        ("base_domain", pa.string()),
        ("is_internal", pa.bool_()),
        ("is_http", pa.bool_()),
        ("element_index", pa.int64()),
    ]
)


def build_navigation_package(
    html: str, *, document_id: str, page_url: str
) -> tuple[bytes, int]:
    grouped = links_from_html(html, page_url=page_url)
    rows = []
    for group, links in grouped.items():
        for link in links:
            url = str(link["href"])
            rows.append(
                {
                    "document_id": document_id,
                    "url": url,
                    "text": str(link.get("text") or ""),
                    "title": str(link.get("title") or ""),
                    "base_domain": str(link.get("base_domain") or ""),
                    "is_internal": group == "internal",
                    "is_http": url.startswith(("http://", "https://")),
                    "element_index": len(rows),
                }
            )
    table = pa.Table.from_pylist(rows, schema=LINKS_SCHEMA)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, LINKS_SCHEMA) as writer:
        writer.write_table(table)
    payload = sink.getvalue().to_pybytes()
    maximum = get_int("ATLAS_NAVIGATION_MAX_PACKAGE_BYTES")
    if len(payload) > maximum:
        raise ValueError(f"navigation package exceeded its {maximum} byte limit")
    return payload, table.num_rows


def navigation_object_name(
    graph_run_id: UUID, document_id: str, page_url: str
) -> str:
    document_hash = document_id.removeprefix("sha256:")
    recipe_hash = sha256(f"{NAVIGATION_RECIPE}\0{page_url}".encode()).hexdigest()
    return (
        f"runtime/navigation/{graph_run_id.hex}/documents/"
        f"{document_hash}/{recipe_hash}.arrow"
    )


def navigation_event_id(crawl_id: UUID, package_sha256: str) -> UUID:
    return uuid5(_EVENT_NAMESPACE, f"{crawl_id}:{package_sha256}")


def put_navigation_package(
    store: ObjectStore, *, name: str, payload: bytes, row_count: int
) -> NavigationPackage:
    digest = sha256(payload).hexdigest()
    import io

    store.put_if_absent(name, io.BytesIO(payload))
    if store.size(name) != len(payload):
        raise RuntimeError("navigation package size changed after publication")
    with store.open(name) as content:
        if sha256(content.read()).hexdigest() != digest:
            raise RuntimeError("navigation package digest changed after publication")
    return NavigationPackage(
        object_name=name,
        sha256=digest,
        schema_version=1,
        recipe=NAVIGATION_RECIPE,
        row_count=row_count,
        byte_size=len(payload),
    )


def load_navigation_package(store: ObjectStore, package: NavigationPackage) -> bytes:
    if store.size(package.object_name) != package.byte_size:
        raise RuntimeError("navigation package size does not match its NATS reference")
    with store.open(package.object_name) as content:
        payload = content.read()
    if sha256(payload).hexdigest() != package.sha256:
        raise RuntimeError("navigation package digest does not match its NATS reference")
    return payload


def delete_run_navigation(store: ObjectStore, graph_run_id: UUID) -> int:
    return store.delete_prefix(f"runtime/navigation/{graph_run_id.hex}/")
