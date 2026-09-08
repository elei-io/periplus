CREATE OR REPLACE VIEW public_v1.html_element AS
SELECT content_sha256 AS content_id, element_index AS node_index, parent_index,
       subtree_end_index, child_index AS sibling_index, depth, tag,
       CASE namespace WHEN 'HTML' THEN 'http://www.w3.org/1999/xhtml'
                      WHEN 'SVG' THEN 'http://www.w3.org/2000/svg'
                      WHEN 'MATHML' THEN 'http://www.w3.org/1998/Math/MathML'
                      WHEN 'NONE' THEN NULL ELSE namespace END AS namespace,
       attributes, text_direct
FROM material.html_elements;
