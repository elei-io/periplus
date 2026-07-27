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

    ``content_sha256`` is deliberately absent. The materialization workload
    supplies it after reading immutable HTML bytes.
    """

    element_index: int
    parent_index: int | None
    subtree_end_index: int
    depth: int
    child_index: int
    tag: str
    namespace_uri: str | None
    attributes: dict[str, str]
    text_direct: str
    text_tail: str


def parse_html(source: str) -> Element:
    """Parse captured HTML into the canonical HTML5 tree."""

    if not isinstance(source, str):
        raise TypeError("source must be a string containing captured HTML")

    return html5lib.parse(
        source,
        treebuilder=_PARSER_OPTIONS["treebuilder"],
        namespaceHTMLElements=_PARSER_OPTIONS["namespace_html_elements"],
    )


def parse_html_bytes(source: bytes) -> Element:
    """Parse exact response bytes using HTML5 encoding detection."""

    if not isinstance(source, bytes):
        raise TypeError("source must be exact HTML bytes")
    return html5lib.parse(
        source,
        treebuilder=_PARSER_OPTIONS["treebuilder"],
        namespaceHTMLElements=_PARSER_OPTIONS["namespace_html_elements"],
    )


def iter_html_elements(source: str) -> Iterator[ElementRow]:
    """Yield deterministic depth-first rows without materializing a row list."""

    yield from iter_tree_elements(parse_html(source))


def iter_html_byte_elements(source: bytes) -> Iterator[ElementRow]:
    """Yield elements from exact response bytes with HTML5 charset sniffing."""

    yield from iter_tree_elements(parse_html_bytes(source))


def iter_tree_elements(root: Element) -> Iterator[ElementRow]:
    """Yield rows for an already parsed HTML5 tree."""

    subtree_sizes: dict[int, int] = {}
    count_stack: list[tuple[Element, bool]] = [(root, False)]
    while count_stack:
        element, visited = count_stack.pop()
        children = [child for child in element if isinstance(child.tag, str)]
        if not visited:
            count_stack.append((element, True))
            count_stack.extend((child, False) for child in reversed(children))
            continue
        subtree_sizes[id(element)] = 1 + sum(
            subtree_sizes[id(child)] for child in children
        )

    next_index = 0
    walk_stack: list[tuple[Element, int | None, int, int, str]] = [
        (root, None, 0, 0, "")
    ]
    while walk_stack:
        element, parent_index, depth, child_index, text_tail = walk_stack.pop()
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
            subtree_end_index=element_index + subtree_sizes[id(element)],
            depth=depth,
            child_index=child_index,
            tag=tag,
            namespace_uri=namespace_uri,
            attributes=attributes,
            text_direct=text or "",
            text_tail=text_tail,
        )
        walk_stack.extend(
            (child, element_index, depth + 1, index, child_tail or "")
            for index, (child, child_tail) in reversed(
                list(enumerate(zip(children, child_tails, strict=True)))
            )
        )


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
