"""Complete HTML5 tree with one document-wide position space."""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    nodes: list[NodeRow] = []
    elements: dict[int, ElementRow] = {}
    # Closing events fill exclusive subtree boundaries without Python recursion.
    stack = [(document, None, 0, 0, False, -1)]
    while stack:
        node, parent, sibling, depth, closing, index = stack.pop()
        if closing:
            nodes[index] = replace(nodes[index], subtree_end_index=len(nodes))
            if index in elements:
                elements[index] = replace(elements[index], subtree_end_index=len(nodes))
            continue
        index = len(nodes)
        kind = kinds[node.nodeType]
        name = (node.localName or node.nodeName) if kind in {"element", "doctype", "processing_instruction"} else None
        nodes.append(NodeRow(index, parent, index + 1, sibling, kind, name,
                             node.namespaceURI, node.nodeValue, depth))
        if kind == "element":
            attributes = {}
            for attribute in node.attributes.values():
                key = (f"{{{attribute.namespaceURI}}}{attribute.localName}"
                       if attribute.namespaceURI else attribute.name)
                attributes[key] = attribute.value
            elements[index] = ElementRow(
                index, parent, index + 1, depth, sibling, name,
                node.namespaceURI, dict(sorted(attributes.items())),
                "".join(child.data for child in node.childNodes if child.nodeType == Node.TEXT_NODE),
                "",
            )
        stack.append((node, parent, sibling, depth, True, index))
        stack.extend((child, index, position, depth + 1, False, -1)
                     for position, child in reversed(list(enumerate(node.childNodes))))
    return tuple(nodes), tuple(elements.values())
