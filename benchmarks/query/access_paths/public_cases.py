"""Native view cases preserve the business-query structure and public fields."""

from pathlib import Path

from client import literal, require, target
from queries import DOCUMENT


def views(layout: str) -> None:
    name = target(layout + "_public_elements")
    if layout.startswith("docs_"):
        select = f"""SELECT d.document_id,e.node_index AS node_index,e.parent_index AS parent_index,
        e.subtree_end_index AS subtree_end_index,e.sibling_index AS sibling_index,e.depth AS depth,
        e.tag AS tag,e.namespace AS namespace,e.attributes AS attributes,e.text_direct AS text_direct,
        substringUTF8(d.document_text,e.text_start+1,e.text_end-e.text_start) AS text
        FROM {target(layout)} d ARRAY JOIN d.elements AS e"""
    elif layout.startswith("elements_full"):
        select = f"""SELECT d.document_id,d.node_index,d.parent_index,d.subtree_end_index,d.sibling_index,
        d.depth,d.tag,d.namespace,d.attributes,d.text_direct,d.text FROM {target(layout)} d"""
    else:
        select = f"""SELECT d.document_id,d.node_index,d.parent_index,d.subtree_end_index,d.sibling_index,
        d.depth,d.tag,d.namespace,d.attributes,d.text_direct,
        substringUTF8(t.document_text,d.text_start+1,d.text_end-d.text_start) AS text
        FROM {target(layout)} d INNER JOIN {target("docs_plain")} t USING(document_id)"""
    select += (
        f" WHERE d.document_id IN (SELECT document_id FROM {target('source_captures')})"
    )
    require(f"CREATE OR REPLACE VIEW {name} AS {select}", read=False)


def cases(layout: str) -> dict[str, str]:
    table = target(layout + "_public_elements")
    return {
        "public_count": f"SELECT count() AS n FROM {table}",
        "public_preview": f"SELECT * FROM {table} LIMIT 10",
        "public_selected_headings": f"SELECT document_id,node_index,text FROM {table} WHERE document_id={literal(DOCUMENT)} AND tag IN ('h1','h2','h3') ORDER BY node_index",
        "public_class": f"SELECT document_id,node_index FROM {table} WHERE has(splitByWhitespace(attributes['class']),'hp-icon') ORDER BY document_id,node_index",
    }


def link_case(layout: str, url: str) -> str:
    capture = target(layout + "_public_capture")
    links = target(layout + "_public_link")
    require(
        f"CREATE OR REPLACE VIEW {capture} AS SELECT capture_id,url,document_id,captured_at FROM {target(layout)}",
        read=False,
    )
    require(
        f"""CREATE OR REPLACE VIEW {links} AS SELECT c.capture_id,l.node_index AS node_index,l.target_url AS target_url,l.raw_href AS raw_href
        FROM {target(layout)} c ARRAY JOIN c.links AS l
        WHERE c.capture_id IN (SELECT capture_id FROM {capture})""",
        read=False,
    )
    original = (
        (
            Path(__file__).resolve().parents[1]
            / "cases/single-capture-links-public/query.sql"
        )
        .read_text()
        .rstrip()
        .rstrip(";")
    )
    return (
        original.replace("public_v1.capture", capture)
        .replace("public_v1.link", links)
        .replace("'http://www.ic3.gov/default.aspx'", literal(url))
    )
