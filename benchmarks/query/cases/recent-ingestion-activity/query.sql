SELECT
    page_visit_id,
    url,
    outcome,
    http_status_code,
    finished_at
FROM web.page_visit
WHERE source_dataset = 'atlas-test-corpus/v3/CC-MAIN-2026-25/20260727'
ORDER BY finished_at DESC
LIMIT 50;
