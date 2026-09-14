-- Diagnostic only: both keys frozen from the selected capture; lookup cost excluded.
SELECT target_url, count(*) AS occurrences
FROM material.link_occurrences
WHERE visit_id = UUID '075dc086-adb8-499c-8796-589ee28136e5'
  AND source_url = 'https://www.ic3.gov/'
GROUP BY target_url ORDER BY occurrences DESC, target_url LIMIT 50;
