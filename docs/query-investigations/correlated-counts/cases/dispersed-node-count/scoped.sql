WITH keys AS MATERIALIZED (SELECT DISTINCT content_id FROM public_v1.capture WHERE effective_url LIKE '%.gov%' AND content_id IS NOT NULL ORDER BY hash(content_id), content_id LIMIT $scope), counts AS MATERIALIZED (
 SELECT n.content_id, count(*) AS nodes FROM public_v1.html_node n SEMI JOIN keys USING (content_id)  GROUP BY n.content_id
)
SELECT k.content_id, coalesce(c.nodes, 0) AS nodes FROM keys k LEFT JOIN counts c USING (content_id);
