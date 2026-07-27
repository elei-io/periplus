"""Stable, deterministic HTML-to-DOM projection."""

from dom.encoder import (
    PARSER_NAME,
    PARSER_OPTIONS_HASH,
    PARSER_VERSION,
    ElementRow,
    encode_html,
    iter_html_byte_elements,
    iter_html_elements,
    iter_tree_elements,
    parse_html,
    parse_html_bytes,
)
from dom.links import (
    DomAnchor,
    GroupedLinkPayload,
    LinkPayload,
    anchors_from_elements,
    links_from_elements,
    links_from_html,
)

__all__ = [
    "PARSER_NAME",
    "PARSER_OPTIONS_HASH",
    "PARSER_VERSION",
    "ElementRow",
    "DomAnchor",
    "GroupedLinkPayload",
    "LinkPayload",
    "anchors_from_elements",
    "encode_html",
    "iter_html_elements",
    "iter_html_byte_elements",
    "iter_tree_elements",
    "links_from_elements",
    "links_from_html",
    "parse_html",
    "parse_html_bytes",
]
