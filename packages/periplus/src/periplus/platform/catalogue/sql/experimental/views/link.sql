CREATE OR REPLACE VIEW experimental.link AS
SELECT visit_id AS capture_id, element_index AS node_index, raw_href, target_url AS resolved_url
FROM material.link_occurrences occurrence
WHERE EXISTS (SELECT 1 FROM experimental.capture capture WHERE capture.capture_id = occurrence.visit_id);
