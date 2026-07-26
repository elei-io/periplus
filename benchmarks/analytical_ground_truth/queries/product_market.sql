WITH selected_crawls AS (
    SELECT
        crawl_id,
        document_id,
        requested_url,
        registrable_domain,
        completed_at,
        status_code,
        outcome
    FROM crawls
    WHERE completed_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00:00'
      AND completed_at < TIMESTAMPTZ '2026-06-15 00:00:00+00:00'
      AND host LIKE 'atlas-retailer-%'
),
json_evidence AS (
    SELECT
        c.crawl_id,
        c.document_id,
        c.requested_url,
        c.registrable_domain,
        c.completed_at,
        json_extract_string(e.text_direct, '$.gtin13') AS gtin,
        json_extract_string(e.text_direct, '$.name') AS product_name,
        CAST(json_extract_string(e.text_direct, '$.offers.price') AS DECIMAL(12, 2)) AS price,
        json_extract_string(e.text_direct, '$.offers.priceCurrency') AS currency,
        json_extract_string(e.text_direct, '$.offers.availability') AS availability
    FROM selected_crawls AS c
    JOIN elements AS e
      ON e.document_id = c.document_id
    WHERE e.tag = 'script'
      AND e.attributes['type'] = 'application/ld+json'
      AND json_extract_string(e.text_direct, '$."@type"') = 'Product'
),
known_offer_urls AS (
    SELECT requested_url, registrable_domain, gtin
    FROM json_evidence
    QUALIFY row_number() OVER (
        PARTITION BY requested_url
        ORDER BY completed_at DESC, crawl_id DESC
    ) = 1
),
latest_crawls AS (
    SELECT *
    FROM selected_crawls
    QUALIFY row_number() OVER (
        PARTITION BY requested_url
        ORDER BY completed_at DESC, crawl_id DESC
    ) = 1
),
latest_offers AS (
    SELECT *
    FROM json_evidence
    QUALIFY row_number() OVER (
        PARTITION BY requested_url
        ORDER BY completed_at DESC, crawl_id DESC
    ) = 1
),
current_offer_state AS (
    SELECT
        known.gtin,
        known.registrable_domain,
        known.requested_url,
        latest_crawl.crawl_id,
        latest_offer.document_id,
        latest_offer.product_name,
        latest_offer.price,
        latest_offer.currency,
        CASE
            WHEN latest_crawl.outcome = 'failed' THEN 'unknown'
            WHEN latest_crawl.status_code = 410 THEN 'removed'
            WHEN latest_offer.availability = 'https://schema.org/InStock' THEN 'in_stock'
            ELSE 'out_of_stock'
        END AS offer_state
    FROM known_offer_urls AS known
    JOIN latest_crawls AS latest_crawl
      USING (requested_url)
    LEFT JOIN latest_offers AS latest_offer
      USING (requested_url)
)
SELECT
    gtin,
    count(*) AS retailer_count,
    count(*) FILTER (WHERE offer_state = 'in_stock') AS in_stock_count,
    count(*) FILTER (WHERE offer_state = 'out_of_stock') AS out_of_stock_count,
    count(*) FILTER (WHERE offer_state = 'removed') AS removed_count,
    count(*) FILTER (WHERE offer_state = 'unknown') AS unknown_count,
    min(price) FILTER (WHERE offer_state = 'in_stock') AS cheapest_price,
    arg_min(registrable_domain, price) FILTER (
        WHERE offer_state = 'in_stock'
    ) AS cheapest_retailer,
    list(crawl_id ORDER BY registrable_domain) AS evidence_crawl_ids,
    list(document_id ORDER BY registrable_domain) AS evidence_document_ids
FROM current_offer_state
GROUP BY gtin
ORDER BY gtin
LIMIT 24;
