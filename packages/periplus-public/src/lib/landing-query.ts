export const landingSql = `WITH pages AS (
  SELECT DISTINCT content_id FROM (
    SELECT content_id FROM capture
    ORDER BY captured_at DESC, capture_id LIMIT 10
  )
)
SELECT e.content_id, e.node_index, e.tag, e.text
FROM html_element e
WHERE e.content_id IN (SELECT content_id FROM pages)
  AND e.tag = 'h1'
ORDER BY e.content_id, e.node_index
LIMIT 100;`
