WITH ranked_content AS (
    SELECT
        page.hostname,
        visit.content_id,
        row_number() OVER (
            PARTITION BY page.hostname
            ORDER BY hash(visit.content_id), visit.content_id
        ) AS host_rank
    FROM web.page AS page
    JOIN web.page_visit AS visit
      ON visit.page_visit_id = page.latest_page_visit_id
    WHERE page.hostname IN (
        'docs.python.org',
        'developer.mozilla.org',
        'commoncrawl.org'
    )
      AND visit.content_id IS NOT NULL
),
scope AS MATERIALIZED (
    SELECT DISTINCT hostname, content_id
    FROM ranked_content
    WHERE host_rank <= $scope
),
element AS (
    SELECT
        content_id,
        element_index,
        parent_index AS parent_element_index,
        subtree_end_index,
        depth,
        child_index AS sibling_index,
        tag AS tag_name,
        namespace,
        attributes,
        text_direct AS direct_text,
        text_tail AS tail_text
    FROM atlas_dom_elements_keyed(
        (SELECT DISTINCT content_id FROM scope)
    )
)
SELECT
    scope.hostname,
    count(*) AS images,
    count_if(
        dom.get_attribute(element.attributes, 'alt') IS NULL
    ) AS missing_alt,
    count_if(
        dom.get_attribute(element.attributes, 'alt') = ''
    ) AS empty_alt
FROM scope
JOIN element USING (content_id)
WHERE element.tag_name = 'img'
GROUP BY scope.hostname
ORDER BY images DESC, scope.hostname;
