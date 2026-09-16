"""Native insert builders; every candidate receives the same retained documents."""

import os
import re

from client import target

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
SOURCE = os.environ.get(
    "PERIPLUS_BENCH_SOURCE", "material_2a70e43637aa42a5932e3caa2654d889"
)
if not re.fullmatch(r"material_[0-9a-f]{32}", SOURCE):
    raise ValueError("Pin one immutable material generation database")


def document_insert(name: str, low: int, high: int) -> str:
    return f"INSERT INTO {target(name)} (document_id,sample_rank,document_text,elements) SELECT lower(hex(document_id)),sample_rank,document_text,elements FROM {target('source_docs')} WHERE sample_rank>{low} AND sample_rank<={high} SETTINGS max_block_size=16"


def element_insert(name: str, low: int, high: int, full: bool) -> str:
    columns = ",".join(FIELDS)
    prefix = ",".join("e." + f for f in FIELDS)
    if full:
        join = "arrayMap(e -> tuple(e,substringUTF8(document_text,e.text_start+1,e.text_end-e.text_start)),elements) AS pair"
        prefix = ",".join("pair.1." + f for f in FIELDS)
        prefix += ",pair.2"
        columns += ",text"
    else:
        join = "elements AS e"
    return f"INSERT INTO {target(name)} (document_id,sample_rank,{columns}) SELECT lower(hex(document_id)),sample_rank,{prefix} FROM {target('source_docs')} ARRAY JOIN {join} WHERE sample_rank>{low} AND sample_rank<={high} SETTINGS max_block_size=4"


def capture_insert(name: str, low: int, high: int) -> str:
    return f"INSERT INTO {target(name)} (capture_id,document_id,sample_rank,url,captured_at,links) SELECT capture_id,document_id,sample_rank,url,captured_at,links FROM {target('source_captures')} WHERE sample_rank>{low} AND sample_rank<={high}"


def json_insert(name: str, low: int, high: int) -> str:
    return f"""INSERT INTO {target(name)} SELECT lower(hex(document_id)),e.node_index,sample_rank,e.text_direct,
    if(JSONType(e.text_direct,'@type')='Array',JSONExtract(e.text_direct,'@type','Array(String)'),[JSONExtractString(e.text_direct,'@type')]),
    JSONExtractString(e.text_direct,'name') FROM {target("source_docs")} ARRAY JOIN elements AS e
    WHERE sample_rank>{low} AND sample_rank<={high} AND e.tag='script' AND e.attributes['type']='application/ld+json'
    AND isValidJSON(e.text_direct) SETTINGS max_block_size=8"""
