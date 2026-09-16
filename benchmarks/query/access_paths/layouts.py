"""Native candidate DDL. Names are isolated; production schema is not modified."""

from client import target

ELEMENT = """node_index UInt32, parent_index Nullable(UInt32), subtree_end_index UInt32,
sibling_index UInt32, depth UInt32, tag String, namespace Nullable(String),
attributes Map(String,String), text_direct String, text_start UInt64, text_end UInt64"""


def documents(name: str, indexed: bool) -> str:
    indexes = (
        """,
    INDEX words lower(document_text) TYPE text(tokenizer=splitByNonAlpha),
    INDEX classes class_tokens TYPE text(tokenizer=array)"""
        if indexed
        else ""
    )
    return f"""CREATE TABLE {target(name)} (
    document_id FixedString(64), sample_rank UInt32,
    document_text String CODEC(ZSTD(3)), elements Array(Tuple({ELEMENT})) CODEC(ZSTD(3)),
    class_tokens Array(String) MATERIALIZED arrayDistinct(arrayFlatten(arrayMap(e -> splitByWhitespace(e.attributes['class']), elements)))
    {indexes}) ENGINE=MergeTree ORDER BY document_id"""


def elements(name: str, full_text: bool, indexed: bool = True) -> str:
    text = ", text String CODEC(ZSTD(3))" if full_text else ""
    flat = (
        ELEMENT.replace("tag String", "tag LowCardinality(String)")
        .replace(
            "attributes Map(String,String)",
            "attributes Map(String,String) CODEC(ZSTD(3))",
        )
        .replace("text_direct String", "text_direct String CODEC(ZSTD(3))")
    )
    index = (
        ", INDEX subtree_words lower(text) TYPE text(tokenizer=splitByNonAlpha)"
        if full_text
        else ""
    )
    indexes = (
        """,
    INDEX classes class_tokens TYPE text(tokenizer=array),
    INDEX direct_words lower(text_direct) TYPE text(tokenizer=splitByNonAlpha),
    PROJECTION by_tag (SELECT tag, document_id, node_index, _part_offset ORDER BY (tag,document_id,node_index)),
    PROJECTION by_id (SELECT attributes['id'] AS id, document_id, node_index, _part_offset ORDER BY (id,document_id,node_index))"""
        if indexed
        else ""
    )
    return f"""CREATE TABLE {target(name)} (
    document_id FixedString(64), sample_rank UInt32,
    {flat},
    class_tokens Array(String) MATERIALIZED splitByWhitespace(attributes['class'])
    {indexes}{text}{index}) ENGINE=MergeTree ORDER BY (document_id,node_index)"""


def captures(name: str, projection: str) -> str:
    columns = (
        "_part_offset"
        if projection == "light"
        else "capture_id,url,host,captured_at,document_id,links"
    )
    extra = (
        f", PROJECTION by_url (SELECT {columns} ORDER BY (url,captured_at,capture_id))"
        if projection != "none"
        else ""
    )
    return f"""CREATE TABLE {target(name)} (
    capture_id UUID,document_id FixedString(64),sample_rank UInt32,
    url String CODEC(ZSTD(3)),host String MATERIALIZED domain(url),captured_at Nullable(DateTime64(6,'UTC')),
    links Array(Tuple(node_index UInt32,raw_href String,target_url String)) CODEC(ZSTD(3))
    {extra}) ENGINE=MergeTree ORDER BY capture_id SETTINGS allow_nullable_key=1"""


def jsonld(name: str, indexed: bool) -> str:
    indexes = (
        ", INDEX types types TYPE text(tokenizer=array), INDEX values raw TYPE text(tokenizer=splitByNonAlpha)"
        if indexed
        else ""
    )
    return f"""CREATE TABLE {target(name)} (
    document_id FixedString(64),node_index UInt32,sample_rank UInt32,
    raw String CODEC(ZSTD(3)), types Array(String), name String,
    PROJECTION by_type (SELECT types,name,document_id,node_index,_part_offset ORDER BY (types,name,document_id,node_index))
    {indexes}) ENGINE=MergeTree ORDER BY (document_id,node_index)"""
