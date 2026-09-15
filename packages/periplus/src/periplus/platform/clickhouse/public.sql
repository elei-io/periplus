CREATE DATABASE IF NOT EXISTS public_v1;
CREATE OR REPLACE VIEW public_v1.capture
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT c.capture_id, c.requested_url AS page_url, c.effective_url,
       c.captured_at, c.http_status AS http_status_code,
       lower(hex(c.content_id)) AS content_id, lower(hex(c.document_id)) AS document_id,
       c.byte_length, c.encoding, c.representation, c.source_provider,
       c.source_dataset, c.source_record_id
FROM material.captures c INNER JOIN material.html_documents d ON c.document_id=d.document_id
WHERE c.completeness='complete';
CREATE OR REPLACE VIEW public_v1.html_element
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT lower(hex(d.content_id)) AS content_id, lower(hex(d.document_id)) AS document_id,
       e.node_index AS node_index, e.parent_index AS parent_index,
       e.subtree_end_index AS subtree_end_index, e.sibling_index AS sibling_index,
       e.depth AS depth, e.tag AS tag, e.namespace AS namespace,
       e.attributes AS attributes, e.text_direct AS text_direct,
       substringUTF8(d.document_text, e.text_start+1, e.text_end-e.text_start) AS text
FROM material.html_documents d ARRAY JOIN d.elements AS e
WHERE d.document_id IN (SELECT document_id FROM material.captures WHERE completeness='complete');
CREATE OR REPLACE VIEW public_v1.link
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT c.capture_id, l.node_index AS node_index, l.target_url AS target_url, l.raw_href AS raw_href
FROM material.captures c ARRAY JOIN c.links AS l
WHERE c.capture_id IN (SELECT capture_id FROM public_v1.capture);
CREATE OR REPLACE VIEW public_v1.page
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT page_url AS url FROM public_v1.capture
UNION DISTINCT SELECT effective_url AS url FROM public_v1.capture WHERE effective_url IS NOT NULL
UNION DISTINCT SELECT target_url AS url FROM public_v1.link;
