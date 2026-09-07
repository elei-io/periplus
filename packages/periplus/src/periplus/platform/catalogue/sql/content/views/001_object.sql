CREATE OR REPLACE VIEW content.object AS
SELECT
    content_sha256 AS content_id,
    min(content_bytes)::BIGINT AS size_bytes,
    min(detected_media_type) AS detected_media_type,
    min(charset) AS detected_character_encoding,
    CASE
        WHEN lower(min(detected_media_type)) IN (
            'text/html', 'application/xhtml+xml'
        ) THEN 'html'
        WHEN lower(min(detected_media_type)) = 'application/json'
          OR lower(min(detected_media_type)) LIKE '%+json' THEN 'json'
        WHEN lower(min(detected_media_type)) = 'application/pdf' THEN 'pdf'
        WHEN lower(min(detected_media_type)) LIKE 'image/%' THEN 'image'
        WHEN lower(min(detected_media_type)) IN (
            'application/xml', 'text/xml'
        )
          OR lower(min(detected_media_type)) LIKE '%+xml' THEN 'xml'
        WHEN lower(min(detected_media_type)) LIKE 'text/%' THEN 'text'
        ELSE 'binary'
    END AS content_format
FROM ingest.documents AS document
WHERE EXISTS (
    SELECT 1 FROM ingest.visits AS visit
    WHERE visit.visit_id = document.visit_id
      AND visit.document_id = document.document_id
      AND visit.visibility = 'public'
)
GROUP BY content_sha256;
