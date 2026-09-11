CREATE OR REPLACE VIEW public_v1.term_node AS
SELECT p.content_sha256 AS content_id, t.text, p.node_index, p.frequency
FROM material.node_posting p
JOIN material.term t USING (term_id);
