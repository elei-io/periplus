CREATE OR REPLACE VIEW public_v1.html_image AS
SELECT content_id, node_index,
       attributes['src'] AS src, attributes['srcset'] AS srcset,
       attributes['sizes'] AS sizes, attributes['alt'] AS alt,
       attributes['width'] AS width, attributes['height'] AS height
FROM public_v1.html_element
WHERE tag = 'img' AND namespace = 'http://www.w3.org/1999/xhtml';
