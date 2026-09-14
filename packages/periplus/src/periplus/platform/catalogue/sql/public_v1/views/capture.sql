CREATE OR REPLACE VIEW public_v1.capture AS
SELECT visit.visit_id AS capture_id, visit.requested_url AS page_url, visit.effective_url,
       visit.observed_at AS captured_at, visit.status_code AS http_status_code,
       document.content_sha256 AS content_id,
       document.content_bytes AS byte_length, document.charset AS encoding
FROM ingest.visits visit JOIN ingest.documents document
  ON document.document_id = visit.document_id AND document.visit_id = visit.visit_id
WHERE lower(document.detected_media_type) = 'text/html';
