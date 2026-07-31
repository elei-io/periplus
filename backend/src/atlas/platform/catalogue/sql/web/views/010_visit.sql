CREATE OR REPLACE VIEW web.visit AS
SELECT
    visit.visit_id,
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
    visit.provenance
FROM ingest.visits AS visit
LEFT JOIN ingest.documents AS document
    ON document.document_id = visit.document_id
   AND document.visit_id = visit.visit_id;
