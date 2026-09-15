SELECT
    capture_id,
    url,
    url,
    http_status_code,
    captured_at
FROM public_v1.capture
WHERE url LIKE 'https://developer.mozilla.org/%'
ORDER BY captured_at DESC NULLS LAST, capture_id DESC
LIMIT 50;
