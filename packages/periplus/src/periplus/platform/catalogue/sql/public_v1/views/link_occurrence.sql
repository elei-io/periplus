CREATE OR REPLACE VIEW public_v1.link_occurrence AS
SELECT visit_id AS capture_id, element_index AS node_index, raw_href, target_url AS resolved_url
FROM material.link_occurrences occurrence
WHERE EXISTS (SELECT 1 FROM public_v1.capture capture WHERE capture.capture_id = occurrence.visit_id);
