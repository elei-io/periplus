CREATE OR REPLACE VIEW web.page_visit AS
SELECT
    visit.visit_id AS page_visit_id,
    visit.crawl_id,
    coalesce(visit.effective_url, visit.requested_url) AS url,
    visit.requested_url,
    visit.effective_url AS final_url,
    visit.admitted_at,
    visit.started_at,
    visit.observed_at,
    visit.finished_at,
    visit.outcome,
    visit.status_code AS http_status_code,
    visit.document_id,
    document.content_sha256 AS content_id,
    document.content_bytes AS content_size_bytes,
    document.representation AS content_representation,
    document.declared_media_type AS declared_content_type,
    document.detected_media_type AS detected_content_type,
    document.charset AS character_encoding,
    visit.provenance.kind AS source_kind,
    visit.provenance.system AS source_system,
    visit.provenance.dataset AS source_dataset,
    visit.provenance.source_record_id AS source_record_id
FROM ingest.visits AS visit
LEFT JOIN ingest.documents AS document
    ON document.document_id = visit.document_id
   AND document.visit_id = visit.visit_id;
