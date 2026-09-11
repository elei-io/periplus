-- Experimental public surface only; not installed in the production registry.
CREATE OR REPLACE VIEW public_v1.term AS
SELECT p.content_id, t.term AS text, p.frequency
FROM material.term_stat p
JOIN material.vocabulary t USING (term_id);
