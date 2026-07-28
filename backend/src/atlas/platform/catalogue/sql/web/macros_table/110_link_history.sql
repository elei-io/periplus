CREATE OR REPLACE MACRO web.link_history(selected_link_id) AS TABLE
SELECT
    observation.link_id,
    link.source_page_id,
    link.target_page_id,
    observation.document_id,
    observation.content_id,
    observation.element_index,
    observation.raw_href,
    observation.observed_at
FROM web.link_observations AS observation
JOIN web.links AS link
    USING (link_id)
WHERE observation.link_id = selected_link_id;
