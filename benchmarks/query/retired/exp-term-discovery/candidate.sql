SELECT content_id
FROM material.term_stat t
JOIN material.vocabulary v USING (term_id)
WHERE v.term IN ('monkeys', 'zoo')
GROUP BY content_id
HAVING count(DISTINCT v.term) = 2
ORDER BY content_id
