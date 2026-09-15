SELECT
    capture_id,
    http_status_code,
    document_id,
    captured_at
FROM public_v1.capture
WHERE url =
      'https://developer.mozilla.org/de/docs/Web/API/AudioNode/context'
ORDER BY captured_at DESC NULLS LAST, capture_id DESC;
