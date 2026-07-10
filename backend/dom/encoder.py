"""HTML5 parsing and the v1 page-local element projection.

The projection follows the parsed browser-style DOM, not source serialization. Comments,
doctypes, processing instructions, attribute order, and entity spelling remain recoverable from
canonical HTML rather than becoming element rows. Text adjacent to omitted non-element nodes is
folded into the surrounding element text segment so structural text queries do not lose it.

Namespaced element names are split into ``tag`` and ``namespace_uri``. Namespaced attribute keys
use ElementTree's stable Clark notation (``{namespace-uri}local-name``); unnamespaced attributes
use their local name unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import version
from xml.etree.ElementTree import Element

import html5lib

from dom.schema import DOM_SCHEMA_VERSION

PARSER_NAME = "html5lib"
PARSER_VERSION = version("html5lib")
_PARSER_OPTIONS = {
    "namespace_html_elements": True,
    "treebuilder": "etree",
}
PARSER_OPTIONS_HASH = hashlib.sha256(
    json.dumps(_PARSER_OPTIONS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class ElementRow:
    """One page-local row in the Atlas DOM schema.

    ``document_id`` is deliberately absent. Repository ingestion injects it after canonical HTML
    has been hashed and stored.
    """

    element_index: int
    parent_index: int | None
    tag: str
    namespace_uri: str | None
    attributes: dict[str, str]
    text: str | None
    tail: str | None


def parse_html(source: str) -> Element:
    """Parse captured HTML into the canonical HTML5 tree."""

    if not isinstance(source, str):
        raise TypeError("source must be a string containing captured HTML")

    return html5lib.parse(
        source,
        treebuilder=_PARSER_OPTIONS["treebuilder"],
        namespaceHTMLElements=_PARSER_OPTIONS["namespace_html_elements"],
    )


def iter_html_elements(source: str) -> Iterator[ElementRow]:
    """Yield deterministic depth-first rows without materializing a row list."""

    yield from iter_tree_elements(parse_html(source))


def iter_tree_elements(root: Element) -> Iterator[ElementRow]:
    """Yield rows for an already parsed HTML5 tree."""

    next_index = 0

    def walk(
        element: Element,
        *,
        parent_index: int | None,
        tail: str | None,
    ) -> Iterator[ElementRow]:
        nonlocal next_index
        element_index = next_index
        next_index += 1
        namespace_uri, tag = _split_expanded_name(element.tag)
        children, text, child_tails = _element_children_and_text(element)
        attributes = {
            key: value
            for key, value in sorted(
                ((_attribute_key(key), value) for key, value in element.attrib.items()),
                key=lambda item: item[0],
            )
        }
        yield ElementRow(
            element_index=element_index,
            parent_index=parent_index,
            tag=tag,
            namespace_uri=namespace_uri,
            attributes=attributes,
            text=text,
            tail=tail,
        )
        for child, child_tail in zip(children, child_tails, strict=True):
            yield from walk(
                child,
                parent_index=element_index,
                tail=child_tail,
            )

    yield from walk(root, parent_index=None, tail=None)


def encode_html(source: str) -> list[ElementRow]:
    """Return all canonical rows for callers that explicitly need a list."""

    return list(iter_html_elements(source))


def _element_children_and_text(
    element: Element,
) -> tuple[list[Element], str | None, list[str | None]]:
    children: list[Element] = []
    child_tails: list[str | None] = []
    before_first = element.text

    for node in element:
        if isinstance(node.tag, str):
            children.append(node)
            child_tails.append(node.tail)
        elif children:
            child_tails[-1] = _join_text(child_tails[-1], node.tail)
        else:
            before_first = _join_text(before_first, node.tail)

    return children, before_first, child_tails


def _split_expanded_name(name: str) -> tuple[str | None, str]:
    if name.startswith("{"):
        namespace_uri, separator, local_name = name[1:].partition("}")
        if separator:
            return namespace_uri, local_name
    return None, name


def _attribute_key(name: str) -> str:
    namespace_uri, local_name = _split_expanded_name(name)
    if namespace_uri is None:
        return local_name
    return f"{{{namespace_uri}}}{local_name}"


def _join_text(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right
