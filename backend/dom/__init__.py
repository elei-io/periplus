"""Stable, deterministic HTML-to-DOM projection."""

from dom.arrow import DomParquet, ELEMENT_ARROW_SCHEMA, element_record_batches, write_dom_parquet
from dom.encoder import (
    PARSER_NAME,
    PARSER_OPTIONS_HASH,
    PARSER_VERSION,
    ElementRow,
    encode_html,
    iter_html_elements,
    iter_tree_elements,
    parse_html,
)
from dom.links import (
    DomAnchor,
    GroupedLinkPayload,
    LinkPayload,
    anchors_from_elements,
    links_from_elements,
    links_from_html,
)
from dom.schema import DOM_SCHEMA_VERSION, ELEMENT_COLUMNS

__all__ = [
    "DOM_SCHEMA_VERSION",
    "DomParquet",
    "ELEMENT_COLUMNS",
    "ELEMENT_ARROW_SCHEMA",
    "PARSER_NAME",
    "PARSER_OPTIONS_HASH",
    "PARSER_VERSION",
    "ElementRow",
    "DomAnchor",
    "GroupedLinkPayload",
    "LinkPayload",
    "anchors_from_elements",
    "encode_html",
    "element_record_batches",
    "iter_html_elements",
    "iter_tree_elements",
    "links_from_elements",
    "links_from_html",
    "parse_html",
    "write_dom_parquet",
]
