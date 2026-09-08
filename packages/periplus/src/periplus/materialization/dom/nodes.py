"""Complete HTML5 tree with one document-wide position space."""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from xml.dom import Node

import html5lib

from periplus.materialization.dom.encoder import ElementRow


@dataclass(frozen=True, slots=True)
class NodeRow:
    node_index: int
    parent_index: int | None
    subtree_end_index: int
    sibling_index: int
    node_type: str
    name: str | None
    namespace: str | None
    value: str | None
    depth: int


def parse_document(source: str | bytes) -> tuple[tuple[NodeRow, ...], tuple[ElementRow, ...]]:
    """Parse once, preserving document, doctype, comment and text nodes.

    Attribute namespaces use Clark notation. Positions describe the HTML5 parsed
    tree, not offsets into source bytes. Adjacent parser text fragments are merged
    so entity/token boundaries do not create artificial text-node identities.
    """
    if not isinstance(source, (str, bytes)):
        raise TypeError("HTML source must be text or bytes")
    document = html5lib.parse(source, treebuilder="dom", namespaceHTMLElements=True)
    document.normalize()
    kinds = {
        Node.DOCUMENT_NODE: "document",
        Node.DOCUMENT_TYPE_NODE: "doctype",
        Node.ELEMENT_NODE: "element",
        Node.TEXT_NODE: "text",
        Node.COMMENT_NODE: "comment",
        Node.PROCESSING_INSTRUCTION_NODE: "processing_instruction",
    }
    nodes: list[NodeRow | None] = []
    elements: list[ElementRow | None] = []
    # Reserve preorder positions on entry; construct final records on exit.
    stack = [(document, None, 0, 0, -1, -1)]
    while stack:
        node, parent, sibling, depth, index, element_index = stack.pop()
        if index < 0:
            index = len(nodes)
            nodes.append(None)
            if node.nodeType == Node.ELEMENT_NODE:
                element_index = len(elements)
                elements.append(None)
            if node.childNodes:
                stack.append((node, parent, sibling, depth, index, element_index))
                stack.extend((child, index, position, depth + 1, -1, -1)
                             for position, child in reversed(list(enumerate(node.childNodes))))
                continue
        kind = kinds[node.nodeType]
        name = (node.localName or node.nodeName) if kind in {"element", "doctype", "processing_instruction"} else None
        nodes[index] = NodeRow(index, parent, len(nodes), sibling, kind, name,
                               node.namespaceURI, node.nodeValue, depth)
        if element_index >= 0:
            attributes = {}
            for attribute in node.attributes.values():
                key = (f"{{{attribute.namespaceURI}}}{attribute.localName}"
                       if attribute.namespaceURI else attribute.name)
                attributes[key] = attribute.value
            elements[element_index] = ElementRow(
                index, parent, len(nodes), depth, sibling, name,
                node.namespaceURI, dict(sorted(attributes.items())),
                # Unlink preserves text data in the direct children retained by
                # this parent, so text can be copied when the parent closes.
                "".join(child.data for child in node.childNodes if child.nodeType == Node.TEXT_NODE),
                "",
            )
        # Children have already been detached, keeping cleanup shallow.
        node.unlink()
    # Every reserved slot is filled before its closing event completes.
    return cast(tuple[NodeRow, ...], tuple(nodes)), cast(tuple[ElementRow, ...], tuple(elements))
