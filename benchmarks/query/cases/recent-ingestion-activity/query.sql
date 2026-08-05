SELECT
    observation_id,
    requested_url,
    effective_url,
    outcome,
    http_status_code,
    observed_at
FROM web.observation
WHERE source_dataset = 'periplus-test-corpus/v4/CC-MAIN-2026-25/20260727'
ORDER BY observed_at DESC NULLS LAST, observation_id DESC
LIMIT 50;
