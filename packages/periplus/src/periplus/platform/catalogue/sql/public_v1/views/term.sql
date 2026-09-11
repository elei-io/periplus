CREATE OR REPLACE VIEW public_v1.term AS
SELECT p.content_sha256 AS content_id, v.term AS text, p.frequency
FROM material.term_stat p
JOIN material.vocabulary v USING (term_id);
