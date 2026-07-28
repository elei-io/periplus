CREATE OR REPLACE VIEW web.documents AS
SELECT
    document.document_id,
    document.visit_id,
    observation.page_id,
    document.content_sha256 AS content_id,
    document.observed_at,
    document.representation,
    document.declared_media_type,
    document.detected_media_type,
    document.charset,
    document.content_bytes
FROM ingest.documents AS document
LEFT JOIN material.page_observations AS observation
    USING (visit_id);
