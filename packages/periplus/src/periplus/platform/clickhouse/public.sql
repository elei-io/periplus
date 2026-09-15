CREATE DATABASE IF NOT EXISTS public_v1;

CREATE OR REPLACE VIEW public_v1.capture
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT i.visit_id AS capture_id, i.requested_url AS page_url, i.effective_url,
       i.observed_at AS captured_at, i.status_code AS http_status_code,
       lower(hex(i.content_sha256)) AS content_id,
       i.content_bytes AS byte_length, i.charset AS encoding
FROM ingest.visits AS i
INNER JOIN material.visit_results AS r
    ON i.visit_id = r.visit_id AND i.evidence_sha256 = r.evidence_sha256
INNER JOIN material.html_documents AS d
    ON d.content_sha256 = r.html_content_sha256 AND d.content_sha256 = i.content_sha256
WHERE lower(i.detected_media_type) = 'text/html';

CREATE OR REPLACE VIEW public_v1.html_element
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT lower(hex(d.content_sha256)) AS content_id,
       element.node_index AS node_index, element.parent_index AS parent_index,
       element.subtree_end_index AS subtree_end_index,
       element.sibling_index AS sibling_index, element.depth AS depth,
       element.tag AS tag, element.namespace AS namespace,
       element.attributes AS attributes, element.text_direct AS text_direct,
       substringUTF8(d.document_text, element.text_start + 1, element.text_end - element.text_start) AS text
FROM material.html_documents AS d
ARRAY JOIN d.elements AS element
WHERE lower(hex(d.content_sha256)) IN (SELECT content_id FROM public_v1.capture);

CREATE OR REPLACE VIEW public_v1.link
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT r.visit_id AS capture_id, link.element_index AS node_index,
       link.target_url AS target_url, link.raw_href AS raw_href
FROM material.visit_results AS r
ARRAY JOIN r.links AS link
WHERE r.visit_id IN (SELECT capture_id FROM public_v1.capture);

CREATE OR REPLACE VIEW public_v1.page
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT page_url AS url FROM public_v1.capture
UNION DISTINCT
SELECT effective_url AS url FROM public_v1.capture WHERE effective_url IS NOT NULL
UNION DISTINCT
SELECT target_url AS url FROM public_v1.link;
