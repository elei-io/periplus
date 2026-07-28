CREATE OR REPLACE VIEW web.content AS
SELECT
    content_sha256 AS content_id,
    content_bytes
FROM ingest.documents
GROUP BY content_sha256, content_bytes;
