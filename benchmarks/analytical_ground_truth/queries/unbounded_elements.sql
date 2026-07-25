SELECT tag, count(*) AS occurrence_count
FROM elements
GROUP BY tag
ORDER BY occurrence_count DESC;
