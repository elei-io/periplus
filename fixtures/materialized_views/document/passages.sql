-- atlas:refresh=keyed(document_id)
CREATE VIEW views.passages AS
WITH candidates AS (
    SELECT
        document_id,
        element_index,
        tag
    FROM elements
    WHERE tag IN (
        'p', 'li', 'blockquote', 'pre',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'td', 'th', 'figcaption'
    )
      AND (macros.has_text(text_direct) OR subtree_end_index > element_index)
)
SELECT
    document_id,
    element_index,
    tag,
    macros.readable_text(document_id, element_index) AS passage
FROM candidates;
