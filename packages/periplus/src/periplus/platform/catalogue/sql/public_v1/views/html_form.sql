CREATE OR REPLACE VIEW public_v1.html_form AS
SELECT content_id, node_index, attributes['id'] AS id, attributes['name'] AS name,
       attributes['action'] AS action, attributes['method'] AS method,
       attributes['enctype'] AS enctype, attributes['target'] AS target
FROM public_v1.html_element
WHERE tag = 'form' AND namespace = 'http://www.w3.org/1999/xhtml';
