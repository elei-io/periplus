CREATE OR REPLACE VIEW web.page_observations AS
SELECT
    page_id,
    visit_id,
    document_id,
    observed_at
FROM material.page_observations;
