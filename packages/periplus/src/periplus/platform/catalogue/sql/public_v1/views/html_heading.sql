CREATE OR REPLACE VIEW public_v1.html_heading AS
SELECT content_id, node_index, substr(tag, 2, 1)::INTEGER AS level, text
FROM public_v1.html_element
WHERE tag IN ('h1','h2','h3','h4','h5','h6') AND namespace = 'http://www.w3.org/1999/xhtml';
