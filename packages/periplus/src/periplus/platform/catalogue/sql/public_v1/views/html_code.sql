CREATE OR REPLACE VIEW public_v1.html_code AS
SELECT c.content_id, c.node_index,
       EXISTS (SELECT 1 FROM public_v1.html_element p
               WHERE p.content_id=c.content_id AND p.tag='pre'
                 AND p.namespace='http://www.w3.org/1999/xhtml'
                 AND p.node_index<c.node_index AND p.subtree_end_index>c.node_index) AS block,
       c.text
FROM public_v1.html_element c
WHERE c.tag='code' AND c.namespace='http://www.w3.org/1999/xhtml';
