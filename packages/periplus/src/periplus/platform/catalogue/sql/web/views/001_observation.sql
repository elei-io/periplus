CREATE OR REPLACE VIEW web.observation AS
SELECT
    visit.visit_id AS observation_id,
    visit.crawl_id,
    visit.requested_url,
    visit.effective_url,
    visit.observed_at,
    visit.outcome,
    visit.status_code AS http_status_code,
    document.content_sha256 AS content_id,
    visit.provenance.kind AS source_kind,
    visit.provenance.system AS source_system,
    visit.provenance.dataset AS source_dataset,
    visit.provenance.source_record_id AS source_record_id
FROM ingest.visits AS visit
LEFT JOIN ingest.documents AS document
    ON document.document_id = visit.document_id
   AND document.visit_id = visit.visit_id;
