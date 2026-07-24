-- atlas:refresh=keyed(document_id)
-- atlas:scope=elements(document_id)
CREATE VIEW views.passages AS
WITH candidates AS (
    SELECT
        document.document_id,
        element.element_index,
        element.tag
    FROM documents AS document
    JOIN elements AS element USING (document_id)
    WHERE element.tag IN (
        'p', 'li', 'blockquote', 'pre',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'td', 'th', 'figcaption'
    )
      AND (
          macros.has_text(element.text_direct)
          OR element.subtree_end_index > element.element_index
      )
)
SELECT
    document_id,
    element_index,
    tag,
    macros.readable_text_scoped(
        document_id,
        element_index,
        macros.materialization_scope('document_id')
    ) AS passage
FROM candidates;
