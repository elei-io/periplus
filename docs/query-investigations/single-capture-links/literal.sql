-- Diagnostic only: the ID is frozen from the selected capture at snapshot 426669.
-- Timings exclude capture selection; do not claim an end-to-end gain from this alone.
SELECT l.target_url, count(*) AS occurrences
FROM experimental.link l
WHERE l.capture_id = UUID '075dc086-adb8-499c-8796-589ee28136e5'
GROUP BY l.target_url ORDER BY occurrences DESC, l.target_url LIMIT 50;
