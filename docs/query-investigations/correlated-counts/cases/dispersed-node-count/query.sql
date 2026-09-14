WITH keys AS MATERIALIZED (SELECT DISTINCT content_id FROM public_v1.capture WHERE effective_url LIKE '%.gov%' AND content_id IS NOT NULL ORDER BY hash(content_id), content_id LIMIT $scope)
SELECT k.content_id, (SELECT count(*) FROM public_v1.html_node n WHERE n.content_id=k.content_id) AS nodes FROM keys k;
