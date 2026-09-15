export const landingSql = `WITH documents AS (
  SELECT DISTINCT document_id FROM (
    SELECT document_id FROM public_v1.capture
    ORDER BY captured_at DESC, capture_id LIMIT 10
  )
)
SELECT e.document_id, e.node_index, e.text AS heading
FROM public_v1.html_element e
WHERE e.document_id IN (SELECT document_id FROM documents)
  AND e.tag = 'h1'
  AND e.text ILIKE '%artificial intelligence%'
ORDER BY e.document_id, e.node_index
LIMIT 100;`
