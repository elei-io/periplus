CREATE OR REPLACE VIEW web.page_stats AS
WITH observation_stats AS (
    SELECT
        observation.page_id,
        count(*) AS visit_count,
        count(observation.document_id) AS document_count,
        count(DISTINCT document.content_sha256) AS distinct_content_count,
        min(observation.observed_at) AS first_observed_at,
        max(observation.observed_at) AS last_observed_at
    FROM material.page_observations AS observation
    LEFT JOIN ingest.documents AS document
        USING (document_id)
    GROUP BY observation.page_id
),
inbound_link_stats AS (
    SELECT
        target_page_id AS page_id,
        count(*) AS inbound_link_count
    FROM material.links
    GROUP BY target_page_id
),
outbound_link_stats AS (
    SELECT
        source_page_id AS page_id,
        count(*) AS outbound_link_count
    FROM material.links
    GROUP BY source_page_id
)
SELECT
    page.page_id,
    coalesce(observation.visit_count, 0) AS visit_count,
    coalesce(observation.document_count, 0) AS document_count,
    coalesce(observation.distinct_content_count, 0)
        AS distinct_content_count,
    observation.first_observed_at,
    observation.last_observed_at,
    coalesce(inbound.inbound_link_count, 0) AS inbound_link_count,
    coalesce(outbound.outbound_link_count, 0) AS outbound_link_count
FROM material.pages AS page
LEFT JOIN observation_stats AS observation
    USING (page_id)
LEFT JOIN inbound_link_stats AS inbound
    USING (page_id)
LEFT JOIN outbound_link_stats AS outbound
    USING (page_id);
