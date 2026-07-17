"""Deterministic link projections derived from Atlas DOM rows.

Fresh acquisitions and durable reprocessing must expose the same link payload. This module is the
single semantic owner, so projections can always be reproduced from retained element rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from io import StringIO
from typing import Protocol
from urllib.parse import urljoin, urlparse
import tldextract

from dom.encoder import iter_html_elements
from control.urls import normalize_url


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def _base_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    value = _TLD_EXTRACT(host)
    return value.top_domain_under_public_suffix or host


class ElementLike(Protocol):
    element_index: int
    parent_index: int | None
    tag: str
    attributes: dict[str, str]
    text_direct: str
    text_tail: str


@dataclass(frozen=True, slots=True)
class DomAnchor:
    """One raw anchor plus its complete descendant text."""

    element_index: int
    href: str
    text: str
    title: str


LinkPayload = dict[str, str | float | None | dict[str, object]]
GroupedLinkPayload = dict[str, list[LinkPayload]]


@dataclass(slots=True)
class _OpenElement:
    element_index: int
    tag: str
    tail: str | None
    anchor: _AnchorAccumulator | None = None


@dataclass(slots=True)
class _AnchorAccumulator:
    element_index: int
    href: str
    title: str
    text: StringIO = field(default_factory=StringIO)


def anchors_from_elements(elements: Iterable[ElementLike]) -> list[DomAnchor]:
    """Return anchors in document order with browser-style subtree text."""

    anchors, _base_url = _scan_elements(elements)
    return anchors


def links_from_elements(
    elements: Iterable[ElementLike],
    *,
    page_url: str,
) -> GroupedLinkPayload:
    """Project canonical link payloads from durable DOM rows."""

    anchors, document_base_url = _scan_elements(elements, page_url=page_url)
    page_base_domain = _base_domain(page_url)
    grouped: GroupedLinkPayload = {"internal": [], "external": []}
    seen: set[str] = set()

    for anchor in anchors:
        try:
            href = normalize_url(urljoin(document_base_url or page_url, anchor.href))
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
        except (TypeError, ValueError):
            continue
        if not href or href in seen:
            continue
        seen.add(href)

        external = _base_domain(href) != page_base_domain
        base_domain = _base_domain(href) if external else page_base_domain
        payload: LinkPayload = {
            "href": href,
            "text": anchor.text,
            "title": anchor.title,
            "base_domain": base_domain,
            "head_data": None,
            "head_extraction_status": None,
            "head_extraction_error": None,
            "intrinsic_score": 0.0,
            "contextual_score": None,
            "total_score": None,
        }
        grouped["external" if external else "internal"].append(payload)
    return grouped


def links_from_html(captured_html: str, *, page_url: str) -> GroupedLinkPayload:
    """Project canonical links directly from captured HTML."""

    return links_from_elements(iter_html_elements(captured_html), page_url=page_url)


def _scan_elements(
    elements: Iterable[ElementLike],
    *,
    page_url: str | None = None,
) -> tuple[list[DomAnchor], str | None]:
    """Project anchors in one pass while retaining only open ancestry and anchor text."""

    stack: list[_OpenElement] = []
    active_anchors: list[_AnchorAccumulator] = []
    finished_anchors: list[DomAnchor] = []
    document_base_url = page_url
    base_selected = False

    def close_until(parent_index: int | None) -> None:
        while stack and stack[-1].element_index != parent_index:
            opened = stack.pop()
            if opened.anchor is not None:
                if not active_anchors or active_anchors[-1] is not opened.anchor:
                    raise ValueError("anchor ancestry is inconsistent")
                active_anchors.pop()
                finished_anchors.append(
                    DomAnchor(
                        element_index=opened.anchor.element_index,
                        href=opened.anchor.href,
                        text=opened.anchor.text.getvalue().strip(),
                        title=opened.anchor.title,
                    )
                )
            if opened.tail:
                for anchor in active_anchors:
                    anchor.text.write(opened.tail)

        actual_parent = stack[-1].element_index if stack else None
        if actual_parent != parent_index:
            raise ValueError("an element parent must be its open document-order ancestor")

    for expected_index, element in enumerate(elements):
        if element.element_index != expected_index:
            raise ValueError("elements must have contiguous zero-based document-order indexes")
        if element.parent_index is not None and not 0 <= element.parent_index < expected_index:
            raise ValueError("an element parent must precede the element in document order")
        close_until(element.parent_index)

        tag = element.tag.lower()
        anchor = None
        if tag == "a":
            href = element.attributes.get("href", "").strip()
            if href:
                anchor = _AnchorAccumulator(
                    element_index=element.element_index,
                    href=href,
                    title=element.attributes.get("title", "").strip(),
                )
                active_anchors.append(anchor)

        opened = _OpenElement(
            element_index=element.element_index,
            tag=tag,
            tail=element.text_tail,
            anchor=anchor,
        )
        stack.append(opened)
        if element.text_direct:
            for active_anchor in active_anchors:
                active_anchor.text.write(element.text_direct)

        if (
            page_url is not None
            and not base_selected
            and tag == "base"
            and any(ancestor.tag == "head" for ancestor in stack[:-1])
        ):
            href = element.attributes.get("href", "").strip()
            if href:
                resolved = urljoin(page_url, href)
                parsed = urlparse(resolved)
                if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                    document_base_url = resolved
                    base_selected = True

    close_until(None)
    finished_anchors.sort(key=lambda anchor: anchor.element_index)
    return finished_anchors, document_base_url
