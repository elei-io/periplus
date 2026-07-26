-- atlas:description=Returns the first structural DOM element matching a CSS selector, optionally restricted to one retained document.
CREATE MACRO macros.query_selector(
    selector,
    document_id := ''
) AS TABLE (
    SELECT * EXCLUDE (match_number)
    FROM (
        SELECT
            element.*,
            row_number() OVER (
                PARTITION BY element.document_id
                ORDER BY element.element_index
            ) AS match_number
        FROM macros.query_selector_all(selector, document_id) AS element
    )
    WHERE match_number = 1
);
