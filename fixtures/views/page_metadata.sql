CREATE VIEW views.page_metadata AS
WITH candidates AS (
    SELECT
        document_id,
        element_index,
        CASE
            WHEN tag = 'html'
            THEN nullif(
                trim(
                    coalesce(
                        get_attribute(attributes, 'lang'),
                        get_attribute(attributes, 'xml:lang')
                    )
                ),
                ''
            )
        END AS language,
        CASE
            WHEN tag = 'title'
            THEN nullif(trim(text_content(document_id, element_index)), '')
        END AS title,
        CASE
            WHEN tag = 'meta'
             AND lower(trim(coalesce(get_attribute(attributes, 'name'), ''))) = 'description'
            THEN nullif(trim(get_attribute(attributes, 'content')), '')
        END AS description,
        CASE
            WHEN tag = 'link'
             AND regexp_matches(
                    lower(coalesce(get_attribute(attributes, 'rel'), '')),
                    '(^|[[:space:]])canonical([[:space:]]|$)'
                 )
            THEN nullif(trim(get_attribute(attributes, 'href')), '')
        END AS canonical_href,
        CASE
            WHEN tag = 'base'
            THEN nullif(trim(get_attribute(attributes, 'href')), '')
        END AS base_href,
        CASE
            WHEN tag = 'meta'
             AND lower(trim(coalesce(get_attribute(attributes, 'name'), ''))) = 'robots'
            THEN nullif(trim(get_attribute(attributes, 'content')), '')
        END AS robots,
        CASE
            WHEN tag = 'meta'
             AND lower(trim(coalesce(get_attribute(attributes, 'property'), ''))) = 'og:title'
            THEN nullif(trim(get_attribute(attributes, 'content')), '')
        END AS open_graph_title,
        CASE
            WHEN tag = 'meta'
             AND lower(trim(coalesce(get_attribute(attributes, 'property'), ''))) = 'og:description'
            THEN nullif(trim(get_attribute(attributes, 'content')), '')
        END AS open_graph_description,
        CASE
            WHEN tag = 'meta'
             AND lower(trim(coalesce(get_attribute(attributes, 'property'), ''))) = 'og:image'
            THEN nullif(trim(get_attribute(attributes, 'content')), '')
        END AS open_graph_image
    FROM elements
    WHERE tag IN ('html', 'title', 'meta', 'link', 'base')
),
metadata AS (
    SELECT
        document_id,
        arg_min(language, element_index) FILTER (WHERE language IS NOT NULL) AS language,
        arg_min(title, element_index) FILTER (WHERE title IS NOT NULL) AS title,
        arg_min(description, element_index) FILTER (WHERE description IS NOT NULL) AS description,
        arg_min(canonical_href, element_index) FILTER (
            WHERE canonical_href IS NOT NULL
        ) AS canonical_href,
        arg_min(base_href, element_index) FILTER (WHERE base_href IS NOT NULL) AS base_href,
        arg_min(robots, element_index) FILTER (WHERE robots IS NOT NULL) AS robots,
        arg_min(open_graph_title, element_index) FILTER (
            WHERE open_graph_title IS NOT NULL
        ) AS open_graph_title,
        arg_min(open_graph_description, element_index) FILTER (
            WHERE open_graph_description IS NOT NULL
        ) AS open_graph_description,
        arg_min(open_graph_image, element_index) FILTER (
            WHERE open_graph_image IS NOT NULL
        ) AS open_graph_image
    FROM candidates
    GROUP BY document_id
)
SELECT
    document.document_id,
    metadata.language,
    metadata.title,
    metadata.description,
    metadata.canonical_href,
    metadata.base_href,
    metadata.robots,
    metadata.open_graph_title,
    metadata.open_graph_description,
    metadata.open_graph_image
FROM documents AS document
LEFT JOIN metadata USING (document_id);
