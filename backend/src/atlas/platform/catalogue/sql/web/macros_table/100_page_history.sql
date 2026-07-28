CREATE OR REPLACE MACRO web.page_history(selected_page_id) AS TABLE
SELECT
    observation.page_id,
    observation.visit_id,
    observation.document_id,
    document.content_id,
    observation.observed_at,
    visit.crawl_id,
    visit.requested_url,
    visit.effective_url,
    visit.outcome,
    visit.status_code
FROM web.page_observations AS observation
JOIN web.visits AS visit
    USING (visit_id)
LEFT JOIN web.documents AS document
    ON document.document_id = observation.document_id
WHERE observation.page_id = selected_page_id;
