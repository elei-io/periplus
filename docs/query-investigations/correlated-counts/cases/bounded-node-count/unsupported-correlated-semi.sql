WITH keys AS MATERIALIZED (SELECT DISTINCT content_id FROM public_v1.capture WHERE effective_url LIKE '%.gov%' AND content_id >= '0' AND content_id < '1' ORDER BY content_id LIMIT $scope)
SELECT k.content_id, (SELECT count(*) FROM public_v1.html_node n SEMI JOIN keys USING (content_id) WHERE n.content_id=k.content_id AND n.content_id >= '0' AND n.content_id < '1') AS nodes FROM keys k;
