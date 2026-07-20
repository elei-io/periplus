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
      AND (
        getvariable('atlas_materialization_document_id') IS NULL
        OR document_id = getvariable('atlas_materialization_document_id')
      )
      AND (macros.has_text(text_direct) OR subtree_end_index > element_index)
)
SELECT
    document_id,
    element_index,
    tag,
    macros.readable_text(document_id, element_index) AS passage
FROM candidates;
