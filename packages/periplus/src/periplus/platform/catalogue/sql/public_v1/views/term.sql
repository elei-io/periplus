CREATE OR REPLACE VIEW public_v1.term AS
SELECT p.content_sha256 AS content_id, v.text AS text, p.frequency
FROM material.content_posting p
JOIN material.term v USING (term_id);
