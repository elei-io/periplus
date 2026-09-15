WITH scope AS MATERIALIZED (
    SELECT DISTINCT document_id
    FROM public_v1.capture
    WHERE url LIKE 'https://developer.mozilla.org/%'
      AND document_id IS NOT NULL
    ORDER BY hash(document_id), document_id
    LIMIT $scope
)
SELECT
    scope.document_id,
    element.node_index,
    map_extract_value(element.attributes, 'href') AS href
FROM scope
JOIN public_v1.html_element AS element USING (document_id)
WHERE element.tag = 'a'
  AND map_contains(element.attributes, 'href')
ORDER BY scope.document_id, element.node_index
LIMIT 5000;
