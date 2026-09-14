CREATE OR REPLACE VIEW experimental.link AS
SELECT visit_id AS capture_id, element_index AS node_index, target_url, raw_href
FROM material.link_occurrences occurrence
WHERE EXISTS (SELECT 1 FROM experimental.capture capture WHERE capture.capture_id = occurrence.visit_id);
