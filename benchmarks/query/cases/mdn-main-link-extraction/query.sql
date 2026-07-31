WITH scope AS MATERIALIZED (
    SELECT DISTINCT visit.content_id
    FROM web.page AS page
    JOIN web.page_visit AS visit
      ON visit.page_visit_id = page.latest_page_visit_id
    WHERE page.hostname = 'developer.mozilla.org'
      AND visit.content_id IS NOT NULL
    ORDER BY hash(visit.content_id), visit.content_id
    LIMIT $scope
)
SELECT
    scope.content_id,
    match.element_index,
    dom.get_attribute(match.attributes, 'href') AS href
FROM scope
JOIN LATERAL dom.query_selector_all(
    scope.content_id,
    'main a[href]'
) AS match ON true
ORDER BY scope.content_id, match.element_index
LIMIT 5000;
