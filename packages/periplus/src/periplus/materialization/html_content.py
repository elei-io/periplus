"""Canonical HTML text and element spans, without repeated descendant strings."""
from collections.abc import Sequence
from pydantic import BaseModel, ConfigDict, Field

from periplus.materialization.dom.encoder import ElementRow
from periplus.materialization.dom.nodes import NodeRow


class HtmlElement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_index: int = Field(ge=0)
    parent_index: int | None
    subtree_end_index: int = Field(gt=0)
    sibling_index: int = Field(ge=0)
    depth: int = Field(ge=0)
    tag: str
    namespace: str | None
    attributes: dict[str, str]
    text_direct: str
    text_start: int = Field(ge=0)
    text_end: int = Field(ge=0)


class HtmlContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_text: str
    elements: tuple[HtmlElement, ...]


def html_content(content_sha256: str, nodes: Sequence[NodeRow], elements: Sequence[ElementRow]) -> HtmlContent:
    prefix = [0]
    pieces = []
    for node in nodes:
        value = (node.value or "") if node.node_type == "text" else ""
        pieces.append(value)
        prefix.append(prefix[-1] + len(value))
    element_ids = {element.element_index for element in elements}
    depths: dict[int, int] = {}
    siblings: dict[int | None, int] = {}
    rows = []
    for element in elements:
        node = nodes[element.element_index]
        parent = node.parent_index
        while parent is not None and parent not in element_ids:
            parent = nodes[parent].parent_index
        depth = 0 if parent is None else depths[parent] + 1
        depths[node.node_index] = depth
        sibling = siblings.get(parent, 0)
        siblings[parent] = sibling + 1
        rows.append(HtmlElement(node_index=node.node_index, parent_index=parent,
            subtree_end_index=node.subtree_end_index, sibling_index=sibling, depth=depth,
            tag=element.tag, namespace=element.namespace_uri, attributes=element.attributes,
            text_direct=element.text_direct, text_start=prefix[node.node_index],
            text_end=prefix[node.subtree_end_index]))
    return HtmlContent(content_sha256=content_sha256, document_text="".join(pieces), elements=tuple(rows))
