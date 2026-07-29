CREATE OR REPLACE VIEW web.visit AS
SELECT
    visit.visit_id,
    observation.page_id,
    page.normalized_url AS url,
    head.visit_id IS NOT NULL AS is_latest,
    visit.crawl_id,
    visit.requested_url,
    visit.effective_url,
    visit.admitted_at,
    visit.started_at,
    visit.observed_at,
    visit.finished_at,
    visit.outcome,
    visit.status_code,
    visit.document_id,
    document.content_sha256 AS content_id,
    document.content_bytes,
    document.representation,
    document.declared_media_type,
    document.detected_media_type,
    document.charset,
    stats.dom_element_count IS NOT NULL AS dom_projection_complete,
    stats.dom_element_count,
    stats.dom_max_depth,
    visit.provenance
FROM ingest.visits AS visit
LEFT JOIN material.page_observations AS observation
    USING (visit_id)
LEFT JOIN material.pages AS page
    USING (page_id)
LEFT JOIN material.page_heads AS head
    USING (page_id, visit_id)
LEFT JOIN ingest.documents AS document
    ON document.document_id = visit.document_id
   AND document.visit_id = visit.visit_id
LEFT JOIN material.content_stats AS stats
    ON stats.content_sha256 = document.content_sha256;
