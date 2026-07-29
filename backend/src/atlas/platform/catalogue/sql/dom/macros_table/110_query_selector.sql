CREATE OR REPLACE MACRO dom.query_selector(
    selected_content_id,
    css_selector
) AS TABLE
SELECT *
FROM atlas_dom_select_first(
    (
        SELECT element.*, false AS _atlas_document_end
        FROM dom.elements AS element
        JOIN (
            SELECT selected_content_id AS content_id
        ) AS selected USING (content_id)
        UNION ALL
        SELECT
            selected_content_id,
            NULL::INTEGER,
            NULL::INTEGER,
            NULL::INTEGER,
            NULL::INTEGER,
            NULL::INTEGER,
            NULL::VARCHAR,
            NULL::VARCHAR,
            NULL::MAP(VARCHAR, VARCHAR),
            NULL::VARCHAR,
            NULL::VARCHAR,
            true
        ORDER BY content_id, _atlas_document_end, element_index
    ),
    css_selector
);
