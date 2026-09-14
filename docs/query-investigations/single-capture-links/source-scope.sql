-- Research only: source_url is derived from the selected capture's effective URL.
-- Keep visit equality authoritative; the URL equality supplies the existing sort key.
WITH selected AS (
 SELECT capture_id, coalesce(effective_url, page_url) AS source_url
 FROM experimental.capture
 WHERE page_url = 'http://www.ic3.gov/default.aspx'
 ORDER BY captured_at DESC, capture_id DESC LIMIT 1
)
SELECT l.target_url, count(*) AS occurrences
FROM material.link_occurrences l JOIN selected s
 ON s.capture_id = l.visit_id AND s.source_url = l.source_url
GROUP BY l.target_url ORDER BY occurrences DESC, l.target_url LIMIT 50;
