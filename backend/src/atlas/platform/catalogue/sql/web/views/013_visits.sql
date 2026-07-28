CREATE OR REPLACE VIEW web.visits AS
SELECT
    visit_id,
    crawl_id,
    requested_url,
    effective_url,
    admitted_at,
    started_at,
    observed_at,
    finished_at,
    outcome,
    status_code,
    document_id,
    provenance
FROM ingest.visits;
