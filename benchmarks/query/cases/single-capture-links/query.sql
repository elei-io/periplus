WITH selected AS (
 SELECT capture_id FROM experimental.capture
 WHERE page_url = 'http://www.ic3.gov/default.aspx'
 ORDER BY captured_at DESC, capture_id DESC LIMIT 1
)
SELECT l.target_url, count(*) AS occurrences
FROM experimental.link l JOIN selected s ON s.capture_id = l.capture_id
GROUP BY l.target_url ORDER BY occurrences DESC, l.target_url LIMIT 50;
