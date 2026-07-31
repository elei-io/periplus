WITH scope AS MATERIALIZED (
    SELECT DISTINCT visit.content_id
    FROM web.page AS page
    JOIN web.page_visit AS visit
      ON visit.page_visit_id = page.latest_page_visit_id
    WHERE page.hostname = 'developer.mozilla.org'
      AND visit.content_id IS NOT NULL
    ORDER BY hash(visit.content_id), visit.content_id
    LIMIT $scope
),
elements AS (
    SELECT
        element.content_id,
        element.element_index,
        element.parent_element_index AS parent_index,
        element.subtree_end_index,
        element.depth,
        element.sibling_index AS child_index,
        element.tag_name AS tag,
        element.namespace,
        element.attributes,
        element.direct_text AS text_direct,
        element.tail_text AS text_tail,
        false AS document_end
    FROM scope
    JOIN dom.element AS element USING (content_id)
),
document_ends AS (
    SELECT
        content_id,
        NULL::INTEGER AS element_index,
        NULL::INTEGER AS parent_index,
        NULL::INTEGER AS subtree_end_index,
        NULL::INTEGER AS depth,
        NULL::INTEGER AS child_index,
        NULL::VARCHAR AS tag,
        NULL::VARCHAR AS namespace,
        NULL::MAP(VARCHAR, VARCHAR) AS attributes,
        NULL::VARCHAR AS text_direct,
        NULL::VARCHAR AS text_tail,
        true AS document_end
    FROM scope
),
document_stream AS (
    FROM elements
    UNION ALL
    FROM document_ends
),
matches AS (
    SELECT match.*
    FROM atlas_dom_select_all(
        (
            SELECT *
            FROM document_stream
            ORDER BY content_id, document_end, element_index
        ),
        'main a[href]'
    ) AS match
)
SELECT
    content_id,
    element_index,
    dom.get_attribute(attributes, 'href') AS href
FROM matches
ORDER BY content_id, element_index
LIMIT 5000;
