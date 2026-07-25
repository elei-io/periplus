"""Deterministic claim-lineage evidence and independent truth oracle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5


PACK_ROOT = Path(__file__).resolve().parent
SCENARIO_NAME = "claim_lineage"
SCENARIO_VERSION = "atlas-analytical-claim-lineage-v1"
_IDENTITY_NAMESPACE = UUID("db654885-41b7-4264-816f-42a1ae3b9d47")
GRAPH_ID = uuid5(_IDENTITY_NAMESPACE, f"{SCENARIO_VERSION}:graph")
GRAPH_RUN_ID = uuid5(_IDENTITY_NAMESPACE, f"{SCENARIO_VERSION}:run")
GRAPH_NODE_ID = uuid5(_IDENTITY_NAMESPACE, f"{SCENARIO_VERSION}:node")
POLICY = {
    "accepted_content_types": ["text/html"],
    "analytical_ground_truth": {
        "pack": SCENARIO_VERSION,
        "scenario": SCENARIO_NAME,
    },
    "completion": {"wait_dynamic": {"enabled": True}},
}
POLICY_JSON = json.dumps(POLICY, separators=(",", ":"), sort_keys=True)
POLICY_HASH = sha256(POLICY_JSON.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Claim:
    index: int
    station: str
    value_ppm: str
    observed_on: str
    page_count: int
    lineage: str
    publishers: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"Station {self.station}|{self.value_ppm}|{self.observed_on}"


@dataclass(frozen=True, slots=True)
class Page:
    index: int
    claim: Claim
    page_index: int
    page_url: str
    domain: str
    publisher: str
    citation_url: str | None
    citation_depth: int
    headline: str
    article_body: str
    html: str

    @property
    def document_id(self) -> str:
        return f"sha256:{sha256(self.html.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class Observation:
    ordinal: int
    observation_index: int
    page: Page
    requested_url: str
    captured_at: datetime
    crawl_id: UUID
    status_code: int
    outcome: Literal["success"]
    html: str

    @property
    def document_id(self) -> str:
        return self.page.document_id


def manifest() -> dict[str, Any]:
    source = _source()
    return {
        "name": SCENARIO_VERSION,
        "scenario": SCENARIO_NAME,
        "start_at": source["start_at"],
        "observation_day_offsets": source["observation_day_offsets"],
        "claim_count": len(source["claims"]),
        "page_count": sum(value["page_count"] for value in source["claims"]),
        "crawl_count": (
            len(source["observation_day_offsets"])
            * sum(value["page_count"] for value in source["claims"])
        ),
        "default_filler_paragraphs": source["default_filler_paragraphs"],
    }


def claims() -> tuple[Claim, ...]:
    return tuple(
        Claim(
            index=index,
            station=value["station"],
            value_ppm=value["value_ppm"],
            observed_on=value["observed_on"],
            page_count=value["page_count"],
            lineage=value["lineage"],
            publishers=tuple(value["publishers"]),
        )
        for index, value in enumerate(_source()["claims"])
    )


def pages(*, filler_elements: int | None = None) -> tuple[Page, ...]:
    source = _source()
    filler_count = (
        source["default_filler_paragraphs"]
        if filler_elements is None
        else filler_elements
    )
    if filler_count < 0 or filler_count > 10_000:
        raise ValueError("filler_elements must be between 0 and 10000")
    result: list[Page] = []
    page_ordinal = 0
    for claim in claims():
        claim_pages: list[Page] = []
        parents = _parents(claim)
        depths = _depths(parents)
        for page_index in range(claim.page_count):
            domain = (
                f"atlas-source-{claim.station.lower()}-"
                f"{page_index + 1:02d}.org"
            )
            page_url = (
                f"https://{domain}/reports/"
                f"station-{claim.station.lower()}-{page_index + 1:02d}"
            )
            citation_url = (
                claim_pages[parents[page_index]].page_url
                if parents[page_index] is not None
                else None
            )
            wording = _wording(claim, page_index)
            headline = (
                f"Field note from Station {claim.station}: "
                f"{claim.value_ppm} ppm"
            )
            html = _render_article(
                headline=headline,
                article_body=wording,
                publisher=claim.publishers[page_index],
                published_on=claim.observed_on,
                citation_url=citation_url,
                filler_count=filler_count,
                seed=page_ordinal,
            )
            page = Page(
                index=page_ordinal,
                claim=claim,
                page_index=page_index,
                page_url=page_url,
                domain=domain,
                publisher=claim.publishers[page_index],
                citation_url=citation_url,
                citation_depth=depths[page_index],
                headline=headline,
                article_body=wording,
                html=html,
            )
            result.append(page)
            claim_pages.append(page)
            page_ordinal += 1
    return tuple(result)


def observations(*, filler_elements: int | None = None) -> tuple[Observation, ...]:
    source = _source()
    start = datetime.fromisoformat(source["start_at"])
    generated_pages = pages(filler_elements=filler_elements)
    result: list[Observation] = []
    ordinal = 0
    for observation_index, day_offset in enumerate(
        source["observation_day_offsets"]
    ):
        for page in generated_pages:
            captured_at = (
                start
                + timedelta(days=day_offset)
                + timedelta(minutes=page.index * 3)
            )
            result.append(
                Observation(
                    ordinal=ordinal,
                    observation_index=observation_index,
                    page=page,
                    requested_url=page.page_url,
                    captured_at=captured_at,
                    crawl_id=uuid5(
                        _IDENTITY_NAMESPACE,
                        (
                            f"{SCENARIO_VERSION}:{page.page_url}:"
                            f"{observation_index}"
                        ),
                    ),
                    status_code=200,
                    outcome="success",
                    html=page.html,
                )
            )
            ordinal += 1
    return tuple(result)


def attempts_for(item: Observation) -> int:
    return 2 if (
        item.page.index * 11 + item.observation_index
    ) % 19 == 0 else 1


def expected_counts(
    source: tuple[Observation, ...] | None = None,
) -> dict[str, int]:
    from dom import iter_html_elements

    source = source or observations()
    html_by_document_id = {
        item.document_id: item.html for item in source
    }
    return {
        "crawls": len(source),
        "documents": len(html_by_document_id),
        "elements": sum(
            sum(1 for _ in iter_html_elements(html))
            for html in html_by_document_id.values()
        ),
        "crawl_attempts": sum(attempts_for(item) for item in source),
        "crawl_steps": len(source),
    }


def expected_rows(
    source: tuple[Observation, ...] | None = None,
) -> list[dict[str, Any]]:
    source = source or observations()
    latest_by_page: dict[str, Observation] = {}
    first_by_page: dict[str, datetime] = {}
    for item in source:
        latest_by_page[item.requested_url] = max(
            item,
            latest_by_page.get(item.requested_url, item),
            key=lambda value: value.captured_at,
        )
        first_by_page[item.requested_url] = min(
            item.captured_at,
            first_by_page.get(item.requested_url, item.captured_at),
        )
    rows: list[dict[str, Any]] = []
    for claim in claims():
        claim_pages = sorted(
            (
                value.page
                for value in latest_by_page.values()
                if value.page.claim.key == claim.key
            ),
            key=lambda value: value.page_url,
        )
        roots = {
            _root_page(value, claim_pages).publisher
            for value in claim_pages
        }
        rows.append(
            {
                "claim_key": claim.key,
                "occurrence_count": len(claim_pages),
                "domain_count": len(
                    {value.domain for value in claim_pages}
                ),
                "publisher_count": len(
                    {value.publisher for value in claim_pages}
                ),
                "independent_origin_count": len(roots),
                "maximum_citation_depth": max(
                    value.citation_depth for value in claim_pages
                ),
                "first_observed_at": min(
                    first_by_page[value.page_url]
                    for value in claim_pages
                ).isoformat(),
                "evidence_crawl_ids": [
                    str(latest_by_page[value.page_url].crawl_id)
                    for value in claim_pages
                ],
                "evidence_document_ids": [
                    value.document_id for value in claim_pages
                ],
            }
        )
    return sorted(
        rows,
        key=lambda value: (
            -value["independent_origin_count"],
            -value["domain_count"],
            value["claim_key"],
        ),
    )


def normalized_result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "claim_key": str(row["claim_key"]),
            "occurrence_count": int(row["occurrence_count"]),
            "domain_count": int(row["domain_count"]),
            "publisher_count": int(row["publisher_count"]),
            "independent_origin_count": int(
                row["independent_origin_count"]
            ),
            "maximum_citation_depth": int(
                row["maximum_citation_depth"]
            ),
            "first_observed_at": row["first_observed_at"].isoformat(),
            "evidence_crawl_ids": [
                str(value) for value in row["evidence_crawl_ids"]
            ],
            "evidence_document_ids": [
                str(value) for value in row["evidence_document_ids"]
            ],
        }
        for row in rows
    ]


def _parents(claim: Claim) -> tuple[int | None, ...]:
    if claim.lineage == "separate_roots":
        return tuple(None for _ in range(claim.page_count))
    if claim.lineage == "deep_copy_tree":
        return (None, 0, 1, 2, 3, 1, 5, 2, 7, 0, 9, 10)
    if claim.lineage == "single_weak_root":
        return (None,) + tuple(0 for _ in range(claim.page_count - 1))
    raise ValueError(f"unknown lineage pattern: {claim.lineage}")


def _depths(parents: tuple[int | None, ...]) -> tuple[int, ...]:
    depths: list[int] = []
    for index, parent in enumerate(parents):
        if parent is not None and parent >= index:
            raise ValueError("citation parents must precede their child")
        depths.append(0 if parent is None else depths[parent] + 1)
    return tuple(depths)


def _root_page(page: Page, claim_pages: list[Page]) -> Page:
    by_url = {value.page_url: value for value in claim_pages}
    current = page
    while current.citation_url is not None:
        current = by_url[current.citation_url]
    return current


def _wording(claim: Claim, page_index: int) -> str:
    values = (
        (
            f"Station {claim.station} recorded {claim.value_ppm} ppm "
            f"on {claim.observed_on}."
        ),
        (
            f"On {claim.observed_on}, instruments at Station "
            f"{claim.station} showed {claim.value_ppm} ppm."
        ),
        (
            f"A reading of {claim.value_ppm} ppm was logged by Station "
            f"{claim.station} on {claim.observed_on}."
        ),
        (
            f"The {claim.observed_on} field log for Station "
            f"{claim.station} reports {claim.value_ppm} ppm."
        ),
    )
    return values[page_index % len(values)]


def _render_article(
    *,
    headline: str,
    article_body: str,
    publisher: str,
    published_on: str,
    citation_url: str | None,
    filler_count: int,
    seed: int,
) -> str:
    template = (
        PACK_ROOT / "templates" / "article_page.html"
    ).read_text(encoding="utf-8")
    article_json = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "articleBody": article_body,
            "datePublished": published_on,
            "headline": headline,
            "publisher": {
                "@type": "Organization",
                "name": publisher,
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    citation = (
        f'<p class="source">Source: <a rel="cite" '
        f'href="{citation_url}">supporting report</a></p>'
        if citation_url is not None
        else ""
    )
    filler = "\n".join(
        (
            f'<p class="context" data-note="{index}">'
            f"Background observation {(seed * 37 + index * 13) % 997}."
            "</p>"
        )
        for index in range(filler_count)
    )
    return template.format(
        headline=headline,
        article_json=article_json,
        article_body=article_body,
        citation=citation,
        filler=filler,
        publisher=publisher,
    )


def _source() -> dict[str, Any]:
    return json.loads(
        (
            PACK_ROOT / "scenarios" / "claim_lineage.json"
        ).read_text(encoding="utf-8")
    )
