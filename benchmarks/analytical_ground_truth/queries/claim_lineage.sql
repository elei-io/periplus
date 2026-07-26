WITH RECURSIVE crawl_history AS (
    SELECT
        crawl_id,
        document_id,
        requested_url,
        registrable_domain,
        completed_at,
        content_captured_at
    FROM crawls
    WHERE completed_at >= TIMESTAMPTZ '2026-06-03 00:00:00+00:00'
      AND completed_at < TIMESTAMPTZ '2026-06-13 00:00:00+00:00'
      AND host LIKE 'atlas-source-%'
      AND outcome = 'success'
),
first_observations AS (
    SELECT
        requested_url,
        min(content_captured_at) AS first_observed_at
    FROM crawl_history
    GROUP BY requested_url
),
latest_crawls AS (
    SELECT
        crawl_id,
        document_id,
        requested_url,
        registrable_domain
    FROM crawl_history
    QUALIFY row_number() OVER (
        PARTITION BY requested_url
        ORDER BY completed_at DESC, crawl_id DESC
    ) = 1
),
dom_evidence AS (
    SELECT
        c.crawl_id,
        c.document_id,
        c.requested_url,
        c.registrable_domain,
        e.tag,
        e.attributes,
        e.text_direct
    FROM latest_crawls AS c
    JOIN elements AS e
      ON e.document_id = c.document_id
    WHERE e.tag IN ('script', 'a')
),
article_evidence AS (
    SELECT
        dom.crawl_id,
        dom.document_id,
        dom.requested_url AS page_url,
        dom.registrable_domain,
        first_seen.first_observed_at,
        json_extract_string(dom.text_direct, '$.articleBody') AS article_body,
        json_extract_string(dom.text_direct, '$.publisher.name') AS publisher
    FROM dom_evidence AS dom
    JOIN first_observations AS first_seen
      USING (requested_url)
    WHERE dom.tag = 'script'
      AND dom.attributes['type'] = 'application/ld+json'
      AND json_extract_string(dom.text_direct, '$."@type"') = 'Article'
),
articles AS (
    SELECT
        crawl_id,
        document_id,
        page_url,
        registrable_domain,
        first_observed_at,
        publisher,
        concat_ws(
            '|',
            regexp_extract(
                article_body,
                '(Station [A-Z][0-9]{2})',
                1
            ),
            regexp_extract(
                article_body,
                '([0-9]+[.][0-9]+) ppm',
                1
            ),
            regexp_extract(
                article_body,
                '(2026-[0-9]{2}-[0-9]{2})',
                1
            )
        ) AS claim_key
    FROM article_evidence
),
citation_edges AS (
    SELECT
        source.claim_key,
        source.page_url AS source_url,
        anchor.attributes['href'] AS target_url
    FROM dom_evidence AS anchor
    JOIN articles AS source
      ON source.page_url = anchor.requested_url
    WHERE anchor.tag = 'a'
      AND anchor.attributes['rel'] = 'cite'
      AND anchor.attributes['href'] IS NOT NULL
),
lineage (
    claim_key,
    descendant_url,
    ancestor_url,
    ancestor_publisher,
    citation_depth
) AS (
    SELECT
        claim_key,
        page_url,
        page_url,
        publisher,
        0
    FROM articles

    UNION ALL

    SELECT
        lineage.claim_key,
        lineage.descendant_url,
        parent.page_url,
        parent.publisher,
        lineage.citation_depth + 1
    FROM lineage
    JOIN citation_edges AS edge
      ON edge.claim_key = lineage.claim_key
     AND edge.source_url = lineage.ancestor_url
    JOIN articles AS parent
      ON parent.claim_key = lineage.claim_key
     AND parent.page_url = edge.target_url
    WHERE lineage.citation_depth < 16
),
root_paths AS (
    SELECT lineage.*
    FROM lineage
    WHERE NOT EXISTS (
        SELECT 1
        FROM citation_edges AS edge
        JOIN articles AS parent
          ON parent.claim_key = lineage.claim_key
         AND parent.page_url = edge.target_url
        WHERE edge.claim_key = lineage.claim_key
          AND edge.source_url = lineage.ancestor_url
    )
),
claim_rollup AS (
    SELECT
        claim_key,
        count(*) AS occurrence_count,
        count(DISTINCT registrable_domain) AS domain_count,
        count(DISTINCT publisher) AS publisher_count,
        min(first_observed_at) AS first_observed_at,
        list(crawl_id ORDER BY page_url) AS evidence_crawl_ids,
        list(document_id ORDER BY page_url) AS evidence_document_ids
    FROM articles
    GROUP BY claim_key
),
lineage_rollup AS (
    SELECT
        claim_key,
        count(DISTINCT ancestor_publisher) AS independent_origin_count,
        max(citation_depth) AS maximum_citation_depth
    FROM root_paths
    GROUP BY claim_key
)
SELECT
    claims.claim_key,
    claims.occurrence_count,
    claims.domain_count,
    claims.publisher_count,
    lineage.independent_origin_count,
    lineage.maximum_citation_depth,
    claims.first_observed_at,
    claims.evidence_crawl_ids,
    claims.evidence_document_ids
FROM claim_rollup AS claims
JOIN lineage_rollup AS lineage
  USING (claim_key)
ORDER BY
    lineage.independent_origin_count DESC,
    claims.domain_count DESC,
    claims.claim_key
LIMIT 3;
