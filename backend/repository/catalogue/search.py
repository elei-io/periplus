"""Registry-driven SQL strategies for exploratory catalogue search."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class SearchType(StrEnum):
    COVERAGE = "coverage"
    PAGES = "pages"
    PASSAGES = "passages"


@dataclass(frozen=True, slots=True)
class SearchStrategy:
    type: SearchType
    label: str
    description: str
    compile_sql: Callable[[str, int], str]


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _coverage_sql(query: str, limit: int) -> str:
    term = _literal(query)
    return f"""WITH matching_pages AS (
    SELECT
        u.host,
        u.registrable_domain,
        c.document_id,
        c.crawl_id,
        c.captured_at
    FROM crawls AS c
    JOIN urls AS u
      ON u.url_id = coalesce(c.final_url_id, c.requested_url_id)
    LEFT JOIN views.page_metadata AS metadata USING (document_id)
    WHERE c.outcome = 'success'
      AND (
        contains(lower(u.normalized_url), lower({term}))
        OR contains(lower(coalesce(metadata.title, '')), lower({term}))
        OR contains(lower(coalesce(metadata.description, '')), lower({term}))
      )
)
SELECT
    host,
    registrable_domain,
    count(DISTINCT document_id) AS matching_documents,
    count(*) AS matching_observations,
    min(captured_at) AS first_seen,
    max(captured_at) AS last_seen
FROM matching_pages
GROUP BY host, registrable_domain
ORDER BY matching_documents DESC, matching_observations DESC, host
LIMIT {limit}"""


def _pages_sql(query: str, limit: int) -> str:
    term = _literal(query)
    return f"""WITH ranked AS (
    SELECT
        c.document_id,
        u.normalized_url AS page_url,
        coalesce(metadata.title, metadata.open_graph_title) AS title,
        coalesce(metadata.description, metadata.open_graph_description) AS description,
        c.captured_at AS last_seen,
        count(*) OVER (PARTITION BY c.document_id, u.normalized_url) AS observations,
        (
            CASE WHEN contains(lower(coalesce(metadata.title, '')), lower({term}))
                THEN 8 ELSE 0 END
            + CASE WHEN contains(lower(coalesce(metadata.description, '')), lower({term}))
                THEN 4 ELSE 0 END
            + CASE WHEN contains(lower(u.normalized_url), lower({term}))
                THEN 2 ELSE 0 END
        ) AS relevance,
        row_number() OVER (
            PARTITION BY c.document_id, u.normalized_url
            ORDER BY c.captured_at DESC
        ) AS recency_rank
    FROM crawls AS c
    JOIN urls AS u
      ON u.url_id = coalesce(c.final_url_id, c.requested_url_id)
    LEFT JOIN views.page_metadata AS metadata USING (document_id)
    WHERE c.outcome = 'success'
      AND (
        contains(lower(u.normalized_url), lower({term}))
        OR contains(lower(coalesce(metadata.title, '')), lower({term}))
        OR contains(lower(coalesce(metadata.description, '')), lower({term}))
      )
)
SELECT
    document_id,
    page_url,
    title,
    description,
    last_seen,
    observations,
    relevance
FROM ranked
WHERE recency_rank = 1
ORDER BY relevance DESC, last_seen DESC
LIMIT {limit}"""


def _passages_sql(query: str, limit: int) -> str:
    term = _literal(query)
    return f"""WITH matches AS (
    SELECT
        passage.document_id,
        passage.element_index,
        passage.tag,
        left(passage.passage, 800) AS passage,
        u.normalized_url AS page_url,
        coalesce(metadata.title, metadata.open_graph_title) AS page_title,
        c.captured_at,
        row_number() OVER (
            PARTITION BY passage.document_id, passage.element_index
            ORDER BY c.captured_at DESC
        ) AS recency_rank
    FROM views.passages AS passage
    JOIN crawls AS c USING (document_id)
    JOIN urls AS u
      ON u.url_id = coalesce(c.final_url_id, c.requested_url_id)
    LEFT JOIN views.page_metadata AS metadata USING (document_id)
    WHERE c.outcome = 'success'
      AND contains(lower(passage.passage), lower({term}))
)
SELECT
    document_id,
    element_index,
    tag,
    passage,
    page_url,
    page_title,
    captured_at
FROM matches
WHERE recency_rank = 1
ORDER BY captured_at DESC, document_id, element_index
LIMIT {limit}"""


SEARCH_TYPES: dict[SearchType, SearchStrategy] = {
    strategy.type: strategy
    for strategy in (
        SearchStrategy(
            type=SearchType.COVERAGE,
            label="Coverage",
            description="See which sources and how much retained evidence match the topic.",
            compile_sql=_coverage_sql,
        ),
        SearchStrategy(
            type=SearchType.PAGES,
            label="Pages",
            description="Find matching page URLs and metadata with crawl provenance.",
            compile_sql=_pages_sql,
        ),
        SearchStrategy(
            type=SearchType.PASSAGES,
            label="Passages",
            description="Find the exact sections of retained pages that mention the topic.",
            compile_sql=_passages_sql,
        ),
    )
}


def compile_search(search_type: SearchType, query: str, limit: int) -> str:
    return SEARCH_TYPES[search_type].compile_sql(query, limit)
