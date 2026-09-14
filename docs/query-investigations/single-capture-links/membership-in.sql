-- Research only: preserve the standalone link membership predicate as an IN semi-join.
WITH links AS (
 SELECT visit_id AS capture_id, element_index AS node_index, target_url, raw_href
 FROM material.link_occurrences
 WHERE visit_id IN (SELECT capture_id FROM experimental.capture)
), selected AS (
 SELECT capture_id FROM experimental.capture
 WHERE page_url = 'http://www.ic3.gov/default.aspx'
 ORDER BY captured_at DESC, capture_id DESC LIMIT 1
)
SELECT l.target_url, count(*) AS occurrences
FROM links l JOIN selected s ON s.capture_id = l.capture_id
GROUP BY l.target_url ORDER BY occurrences DESC, l.target_url LIMIT 50;
