"""Complete HTML5 tree with one document-wide position space."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from periplus.materialization.dom.encoder import ElementRow
from periplus.materialization.dom.lexbor import records


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


@dataclass(slots=True)
class _Frame:
    index: int
    parent: int | None
    sibling: int
    depth: int
    kind: str
    name: str | None
    namespace: str | None
    value: str | None
    attributes: dict[str, str]
    element_index: int
    children: int = 0
    text: list[str] = field(default_factory=list)


def parse_document(
    source: str | bytes,
) -> tuple[tuple[NodeRow, ...], tuple[ElementRow, ...]]:
    """Lexbor preorder identities, including explicit template content fragments.

    Subtree ends are exclusive. Comments retain exact parsed character data;
    text_direct contains only immediate text children, never descendant text.
    """
    nodes: list[NodeRow | None] = []
    elements: list[ElementRow | None] = []
    stack: list[_Frame] = []
    for record in records(source):
        if record is not None:
            kind, name, namespace, value, attributes = record
            parent = stack[-1] if stack else None
            frame = _Frame(
                len(nodes),
                parent.index if parent else None,
                parent.children if parent else 0,
                len(stack),
                kind,
                name,
                namespace,
                value,
                attributes,
                len(elements) if kind == "element" else -1,
            )
            nodes.append(None)
            if kind == "element":
                elements.append(None)
            if parent:
                parent.children += 1
                if kind == "text" and parent.kind == "element":
                    parent.text.append(value or "")
            stack.append(frame)
        else:
            frame = stack.pop()
            nodes[frame.index] = NodeRow(
                frame.index,
                frame.parent,
                len(nodes),
                frame.sibling,
                frame.kind,
                frame.name,
                frame.namespace,
                frame.value,
                frame.depth,
            )
            if frame.element_index >= 0:
                elements[frame.element_index] = ElementRow(
                    frame.index,
                    frame.parent,
                    len(nodes),
                    frame.depth,
                    frame.sibling,
                    frame.name,
                    frame.namespace,
                    frame.attributes,
                    "".join(frame.text),
                    "",
                )
    return cast(tuple[NodeRow, ...], tuple(nodes)), cast(
        tuple[ElementRow, ...], tuple(elements)
    )
