"""Ephemeral, reproducible navigation packages for bounded frontier selection."""

from __future__ import annotations

from hashlib import sha256
from typing import cast

import pyarrow as pa
from periplus.platform.config import get_int
from periplus.materialization.dom import PARSER_NAME, PARSER_OPTIONS_HASH, PARSER_VERSION, links_from_html
from periplus.ingestion.objects.store import ObjectStore
from periplus.crawl.runtime.navigation_contract import NavigationPackage

NAVIGATION_RECIPE = sha256(
    f"{PARSER_NAME}:{PARSER_VERSION}:{PARSER_OPTIONS_HASH}:nav-links-v8".encode()
).hexdigest()

LINKS_SCHEMA = pa.schema(
    [
        ("content_sha256", pa.string()),
        ("source_url", pa.string()),
        ("source_scheme", pa.string()),
        ("source_host", pa.string()),
        ("source_port", pa.int32()),
        ("source_registrable_domain", pa.string()),
        ("source_path", pa.string()),
        ("source_query", pa.string()),
        ("target_url", pa.string()),
        ("target_scheme", pa.string()),
        ("target_host", pa.string()),
        ("target_port", pa.int32()),
        ("target_path", pa.string()),
        ("target_query", pa.string()),
        ("target_fragment", pa.string()),
        ("relation_scope", pa.string()),
        ("raw_href", pa.string()),
        ("element_index", pa.int64()),
    ]
)


def build_navigation_package(
    html: str, *, content_sha256: str, page_url: str
) -> tuple[bytes, int]:
    grouped = links_from_html(html, page_url=page_url)
    rows = []
    for links in grouped.values():
        for link in links:
            relation_kind = str(link["relation_kind"])
            rows.append(
                {
                    "content_sha256": content_sha256,
                    "source_url": str(link["source_url"]),
                    "source_scheme": str(link["source_scheme"]),
                    "source_host": str(link["source_host"]),
                    "source_port": int(link["source_port"]),
                    "source_registrable_domain": str(
                        link["source_registrable_domain"]
                    ),
                    "source_path": str(link["source_path"]),
                    "source_query": link["source_query"],
                    "target_url": str(link["target_url"]),
                    "target_scheme": str(link["target_scheme"]),
                    "target_host": str(link["target_host"]),
                    "target_port": int(link["target_port"]),
                    "target_path": str(link["target_path"]),
                    "target_query": link["target_query"],
                    "target_fragment": link["target_fragment"],
                    "relation_scope": (
                        "self"
                        if relation_kind == "same_url"
                        else "same_origin"
                        if relation_kind in {"same_path", "same_origin"}
                        else relation_kind
                    ),
                    "raw_href": str(link.get("raw_href") or ""),
                    "element_index": int(link["element_index"]),
                }
            )
    rows.sort(key=lambda row: cast(int, row["element_index"]))
    table = pa.Table.from_pylist(rows, schema=LINKS_SCHEMA)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, LINKS_SCHEMA) as writer:
        writer.write_table(table)
    payload = sink.getvalue().to_pybytes()
    maximum = get_int("PERIPLUS_NAVIGATION_MAX_PACKAGE_BYTES")
    if len(payload) > maximum:
        raise ValueError(f"navigation package exceeded its {maximum} byte limit")
    return payload, table.num_rows


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
        schema_version=6,
        recipe=NAVIGATION_RECIPE,
        row_count=row_count,
        byte_size=len(payload),
    )


def load_navigation_package(store: ObjectStore, package: NavigationPackage) -> bytes:
    if store.size(package.object_name) != package.byte_size:
        raise RuntimeError("navigation package size does not match its recorded reference")
    with store.open(package.object_name) as content:
        payload = content.read()
    if sha256(payload).hexdigest() != package.sha256:
        raise RuntimeError("navigation package digest does not match its recorded reference")
    return payload
