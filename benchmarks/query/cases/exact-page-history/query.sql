SELECT
    observation_id,
    outcome,
    http_status_code,
    content_id,
    observed_at
FROM web.observation
WHERE coalesce(effective_url, requested_url) =
      'https://developer.mozilla.org/de/docs/Web/API/AudioNode/context'
ORDER BY observed_at DESC NULLS LAST, observation_id DESC;
