CREATE OR REPLACE VIEW public_v1.prose AS
SELECT content_sha256 AS content_id, text
FROM material.prose;
