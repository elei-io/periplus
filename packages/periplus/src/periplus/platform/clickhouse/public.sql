CREATE DATABASE IF NOT EXISTS public_v1;
CREATE OR REPLACE VIEW public_v1.capture
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT c.capture_id, c.url,
       c.captured_at, c.http_status AS http_status_code,
       c.document_id AS document_id,
       c.byte_length, c.encoding, d.document_text AS text, d.element_count
FROM material.captures c INNER JOIN material.html_documents d ON c.document_id=d.document_id
WHERE c.completeness='complete';
CREATE OR REPLACE VIEW public_v1.html_element
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT e.document_id AS document_id,
       e.node_index, e.parent_index, e.subtree_end_index, e.sibling_index,
       e.depth, e.tag, e.namespace, e.attributes, e.text_direct, e.text
FROM material.html_elements e
WHERE e.document_id IN (SELECT document_id FROM material.html_documents)
  AND e.document_id IN (SELECT document_id FROM material.captures WHERE completeness='complete');
CREATE OR REPLACE VIEW public_v1.link
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT c.capture_id, l.node_index AS node_index, l.target_url AS target_url, l.raw_href AS raw_href
FROM material.captures c ARRAY JOIN c.links AS l
WHERE c.capture_id IN (SELECT capture_id FROM public_v1.capture);
CREATE OR REPLACE VIEW public_v1.page
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT url FROM public_v1.capture
UNION DISTINCT SELECT target_url AS url FROM public_v1.link;

CREATE OR REPLACE VIEW public_v1.html_json_ld
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT j.document_id AS document_id, j.node_index, j.json, j.types, j.name
FROM material.json_ld j
WHERE j.document_id IN (SELECT document_id FROM material.html_documents)
  AND j.document_id IN (SELECT document_id FROM material.captures WHERE completeness='complete');
