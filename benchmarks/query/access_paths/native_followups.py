"""Small native follow-ups: attribute projection, JSON name, compact part packing."""

from client import target
from layouts import documents, jsonld


def json_name_ddl() -> str:
    ddl = jsonld("json_name", True)
    return ddl.replace(
        "PROJECTION by_type (SELECT types,name,document_id,node_index,_part_offset ORDER BY (types,name,document_id,node_index))",
        "PROJECTION by_name (SELECT name,document_id,node_index,_part_offset ORDER BY (name,document_id,node_index))",
    )


def packed_documents_ddl() -> str:
    return (
        documents("docs_indexed_packed", True)
        + " SETTINGS min_bytes_for_wide_part=268435456"
    )


def lean_elements_ddl() -> str:
    from layouts import elements

    ddl = elements("elements_lean", False)
    ddl = ddl.replace(
        "class_tokens Array(String) MATERIALIZED splitByWhitespace(attributes['class'])",
        "class_tokens Array(String) ALIAS splitByWhitespace(attributes['class']), element_id String MATERIALIZED attributes['id']",
    )
    ddl = ddl.replace(
        "PROJECTION by_id (SELECT attributes['id'] AS id, document_id, node_index, _part_offset ORDER BY (id,document_id,node_index))",
        "PROJECTION by_id (SELECT _part_offset ORDER BY (element_id,document_id,node_index))",
    )
    ddl = ddl.replace(
        "PROJECTION by_tag",
        "PROJECTION by_document (SELECT document_id,count() GROUP BY document_id), PROJECTION by_tag",
    )
    return ddl + " SETTINGS min_bytes_for_wide_part=268435456"


def lean_full_elements_ddl() -> str:
    ddl = lean_elements_ddl().replace(
        target("elements_lean"), target("elements_full_lean")
    )
    return ddl.replace(
        "class_tokens Array(String)",
        "text String CODEC(ZSTD(3)), INDEX subtree_words lower(text) TYPE text(tokenizer=splitByNonAlpha), class_tokens Array(String)",
    )


def keyword_capture_ddl() -> str:
    from layouts import captures

    return captures("captures_keyword", "none").replace(
        ") ENGINE=MergeTree ORDER BY capture_id",
        ", INDEX url_exact url TYPE text(tokenizer=array)) ENGINE=MergeTree ORDER BY capture_id",
    )


def attribute_probe_ddl() -> str:
    return f"""CREATE TABLE {target("attributes_probe")} (
        document_id FixedString(64),node_index UInt32,sample_rank UInt32,
        attributes Map(String,String) CODEC(ZSTD(3)),
        INDEX keys mapKeys(attributes) TYPE text(tokenizer=array),
        INDEX values mapValues(attributes) TYPE text(tokenizer=array)
    ) ENGINE=MergeTree ORDER BY (document_id,node_index)
    SETTINGS min_bytes_for_wide_part=268435456"""
