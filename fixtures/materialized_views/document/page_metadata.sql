CREATE VIEW views.page_metadata AS
WITH raw_candidates AS (
    SELECT
        document_id,
        element_index,
        CASE
            WHEN tag = 'html'
            THEN coalesce(
                macros.get_attribute(attributes, 'lang'),
                macros.get_attribute(attributes, 'xml:lang')
            )
        END AS language,
        CASE
            WHEN tag = 'title'
            THEN macros.text_content(document_id, element_index)
        END AS title,
        CASE
            WHEN tag = 'meta'
             AND lower(
                    trim(
                        regexp_replace(
                            coalesce(macros.get_attribute(attributes, 'name'), ''),
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    )
                 ) = 'description'
            THEN macros.get_attribute(attributes, 'content')
        END AS description,
        CASE
            WHEN tag = 'link'
             AND regexp_matches(
                    lower(coalesce(macros.get_attribute(attributes, 'rel'), '')),
                    '(^|[[:space:]])canonical([[:space:]]|$)'
                 )
            THEN macros.get_attribute(attributes, 'href')
        END AS canonical_href,
        CASE
            WHEN tag = 'base'
            THEN macros.get_attribute(attributes, 'href')
        END AS base_href,
        CASE
            WHEN tag = 'meta'
             AND lower(
                    trim(
                        regexp_replace(
                            coalesce(macros.get_attribute(attributes, 'name'), ''),
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    )
                 ) = 'robots'
            THEN macros.get_attribute(attributes, 'content')
        END AS robots,
        CASE
            WHEN tag = 'meta'
             AND lower(
                    trim(
                        regexp_replace(
                            coalesce(macros.get_attribute(attributes, 'property'), ''),
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    )
                 ) = 'og:title'
            THEN macros.get_attribute(attributes, 'content')
        END AS open_graph_title,
        CASE
            WHEN tag = 'meta'
             AND lower(
                    trim(
                        regexp_replace(
                            coalesce(macros.get_attribute(attributes, 'property'), ''),
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    )
                 ) = 'og:description'
            THEN macros.get_attribute(attributes, 'content')
        END AS open_graph_description,
        CASE
            WHEN tag = 'meta'
             AND lower(
                    trim(
                        regexp_replace(
                            coalesce(macros.get_attribute(attributes, 'property'), ''),
                            '[ \t\r\n\f]+',
                            ' ',
                            'g'
                        )
                    )
                 ) = 'og:image'
            THEN macros.get_attribute(attributes, 'content')
        END AS open_graph_image
    FROM elements
    WHERE tag IN ('html', 'title', 'meta', 'link', 'base')
),
candidates AS (
    SELECT
        document_id,
        element_index,
        nullif(
            trim(regexp_replace(language, '[ \t\r\n\f]+', ' ', 'g')),
            ''
        ) AS language,
        nullif(
            trim(regexp_replace(title, '[ \t\r\n\f]+', ' ', 'g')),
            ''
        ) AS title,
        nullif(
            trim(regexp_replace(description, '[ \t\r\n\f]+', ' ', 'g')),
            ''
        ) AS description,
        nullif(
            regexp_replace(
                regexp_replace(canonical_href, '^[ \t\r\n\f]+', ''),
                '[ \t\r\n\f]+$',
                ''
            ),
            ''
        ) AS canonical_href,
        nullif(
            regexp_replace(
                regexp_replace(base_href, '^[ \t\r\n\f]+', ''),
                '[ \t\r\n\f]+$',
                ''
            ),
            ''
        ) AS base_href,
        nullif(
            trim(regexp_replace(robots, '[ \t\r\n\f]+', ' ', 'g')),
            ''
        ) AS robots,
        nullif(
            trim(regexp_replace(open_graph_title, '[ \t\r\n\f]+', ' ', 'g')),
            ''
        ) AS open_graph_title,
        nullif(
            trim(
                regexp_replace(
                    open_graph_description,
                    '[ \t\r\n\f]+',
                    ' ',
                    'g'
                )
            ),
            ''
        ) AS open_graph_description,
        nullif(
            regexp_replace(
                regexp_replace(open_graph_image, '^[ \t\r\n\f]+', ''),
                '[ \t\r\n\f]+$',
                ''
            ),
            ''
        ) AS open_graph_image
    FROM raw_candidates
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
