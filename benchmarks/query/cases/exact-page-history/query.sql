SELECT
    page_visit_id,
    outcome,
    http_status_code,
    content_id,
    content_size_bytes,
    finished_at
FROM web.page_visit
WHERE url = 'https://developer.mozilla.org/de/docs/Web/API/AudioNode/context'
ORDER BY finished_at DESC;
