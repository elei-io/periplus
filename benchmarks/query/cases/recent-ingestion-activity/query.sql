SELECT
    capture_id,
    requested_url,
    effective_url,
    http_status_code,
    captured_at
FROM public_v1.capture
WHERE effective_url LIKE 'https://developer.mozilla.org/%'
ORDER BY captured_at DESC NULLS LAST, capture_id DESC
LIMIT 50;
