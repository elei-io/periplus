WITH scope AS MATERIALIZED (
    SELECT DISTINCT content_id
    FROM web.observation
    WHERE effective_url LIKE 'https://developer.mozilla.org/%'
      AND content_id IS NOT NULL
    ORDER BY hash(content_id), content_id
    LIMIT $scope
)
SELECT
    scope.content_id,
    element.element_index,
    map_extract_value(element.attributes, 'href') AS href
FROM scope
JOIN content.html_element AS element USING (content_id)
WHERE element.tag = 'a'
  AND map_contains(element.attributes, 'href')
ORDER BY scope.content_id, element.element_index
LIMIT 5000;
