"""Deterministic link projections derived from Periplus DOM rows.

Fresh acquisitions and durable reprocessing must expose the same link payload. This module is the
single semantic owner, so projections can always be reproduced from retained element rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse, urlsplit
import tldextract

from periplus.materialization.dom.encoder import iter_html_elements
from periplus.urls import normalize_url


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def _base_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    value = _TLD_EXTRACT(host)
    return value.top_domain_under_public_suffix or host


class ElementLike(Protocol):
    element_index: int
    parent_index: int | None
    subtree_end_index: int
    tag: str
    attributes: dict[str, str]
    text_direct: str
    text_tail: str


@dataclass(frozen=True, slots=True)
class DomAnchor:
    """One raw anchor occurrence."""

    element_index: int
    href: str


LinkPayload = dict[str, object]
GroupedLinkPayload = dict[str, list[LinkPayload]]


@dataclass(slots=True)
class _OpenElement:
    element_index: int
    subtree_end_index: int
    tag: str


def anchors_from_elements(elements: Iterable[ElementLike]) -> list[DomAnchor]:
    """Return raw anchor occurrences in document order."""

    anchors, _base_url = _scan_elements(elements)
    return anchors


def links_from_elements(
    elements: Iterable[ElementLike],
    *,
    page_url: str,
) -> GroupedLinkPayload:
    """Project canonical link payloads from durable DOM rows."""

    anchors, document_base_url = _scan_elements(elements, page_url=page_url)
    source_url = normalize_url(page_url)
    source_parts = urlsplit(source_url)
    source_host = source_parts.hostname or ""
    source_port = source_parts.port or {"http": 80, "https": 443}[source_parts.scheme]
    source_registrable_domain = _base_domain(source_url)
    grouped: GroupedLinkPayload = {"internal": [], "external": []}

    for anchor in anchors:
        try:
            resolved_url = urljoin(document_base_url or page_url, anchor.href)
            target_url = normalize_url(resolved_url)
            target_parts = urlsplit(target_url)
            if target_parts.scheme not in {"http", "https"} or not target_parts.netloc:
                continue
        except (TypeError, ValueError):
            continue
        if not target_url:
            continue

        target_host = target_parts.hostname or ""
        target_port = target_parts.port or {
            "http": 80,
            "https": 443,
        }[target_parts.scheme]
        same_origin = (
            target_parts.scheme,
            target_host,
            target_port,
        ) == (
            source_parts.scheme,
            source_host,
            source_port,
        )
        if target_url == source_url:
            relation_kind = "same_url"
        elif same_origin and target_parts.path == source_parts.path:
            relation_kind = "same_path"
        elif same_origin:
            relation_kind = "same_origin"
        elif target_host == source_host:
            relation_kind = "same_host"
        elif (
            target_host == source_registrable_domain
            or target_host.endswith(f".{source_registrable_domain}")
        ):
            relation_kind = "same_site"
        else:
            relation_kind = "external"
        payload: LinkPayload = {
            "raw_href": anchor.href,
            "source_url": source_url,
            "source_scheme": source_parts.scheme,
            "source_host": source_host,
            "source_port": source_port,
            "source_registrable_domain": source_registrable_domain,
            "source_path": source_parts.path,
            "source_query": source_parts.query or None,
            "target_url": target_url,
            "target_scheme": target_parts.scheme,
            "target_host": target_host,
            "target_port": target_port,
            "target_path": target_parts.path,
            "target_query": target_parts.query or None,
            "target_fragment": urlsplit(resolved_url).fragment or None,
            "relation_kind": relation_kind,
            "element_index": anchor.element_index,
        }
        grouped["external" if relation_kind == "external" else "internal"].append(
            payload
        )
    return grouped


def links_from_html(captured_html: str, *, page_url: str) -> GroupedLinkPayload:
    """Project canonical links directly from captured HTML."""

    return links_from_elements(iter_html_elements(captured_html), page_url=page_url)


def _scan_elements(
    elements: Iterable[ElementLike],
    *,
    page_url: str | None = None,
) -> tuple[list[DomAnchor], str | None]:
    """Project anchor provenance in one pass while retaining only open ancestry."""

    stack: list[_OpenElement] = []
    finished_anchors: list[DomAnchor] = []
    document_base_url = page_url
    base_selected = False
    previous_index = -1

    for element in elements:
        if element.element_index <= previous_index:
            raise ValueError("elements must have increasing document-order indexes")
        previous_index = element.element_index
        if element.parent_index is not None and not 0 <= element.parent_index < element.element_index:
            raise ValueError("an element parent must precede the element in document order")
        # Element rows omit non-element ancestors, notably template.content
        # document fragments. Complete subtree ranges preserve that ancestry.
        while stack and element.element_index >= stack[-1].subtree_end_index:
            stack.pop()
        if element.subtree_end_index <= element.element_index:
            raise ValueError("an element subtree must end after its start")
        if stack and element.subtree_end_index > stack[-1].subtree_end_index:
            raise ValueError("an element subtree must be contained by its ancestor")

        tag = element.tag.lower()
        if tag == "a":
            href = element.attributes.get("href") or ""
            if href.strip():
                finished_anchors.append(
                    DomAnchor(
                        element_index=element.element_index,
                        href=href,
                    )
                )

        opened = _OpenElement(
            element_index=element.element_index,
            subtree_end_index=element.subtree_end_index,
            tag=tag,
        )
        stack.append(opened)

        if (
            page_url is not None
            and not base_selected
            and tag == "base"
            and any(ancestor.tag == "head" for ancestor in stack[:-1])
        ):
            href = (element.attributes.get("href") or "").strip()
            if href:
                resolved = urljoin(page_url, href)
                parsed = urlparse(resolved)
                if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                    document_base_url = resolved
                    base_selected = True

    finished_anchors.sort(key=lambda anchor: anchor.element_index)
    return finished_anchors, document_base_url
