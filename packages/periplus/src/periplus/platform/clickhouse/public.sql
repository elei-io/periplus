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

CREATE OR REPLACE VIEW public_v1.html_jsonld
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
SELECT j.document_id AS document_id, j.node_index, j.json, j.types, j.name
FROM material.json_ld j
WHERE j.document_id IN (SELECT document_id FROM material.html_documents)
  AND j.document_id IN (SELECT document_id FROM material.captures WHERE completeness='complete');

CREATE OR REPLACE VIEW public_v1.html_metadata
DEFINER = CURRENT_USER SQL SECURITY DEFINER AS
WITH elements AS (
    SELECT document_id, node_index, tag, attributes, text_direct
    FROM public_v1.html_element
    WHERE namespace='http://www.w3.org/1999/xhtml'
)
SELECT document_id, node_index, 'title' AS source,
       CAST(NULL AS Nullable(String)) AS attribute, 'title' AS name,
       text_direct AS value
FROM elements WHERE tag='title'
UNION ALL
SELECT document_id, node_index, 'meta', 'name', attributes['name'],
       if(mapContains(attributes,'content'),attributes['content'],NULL)
FROM elements WHERE tag='meta' AND mapContains(attributes,'name')
UNION ALL
SELECT document_id, node_index, 'meta', 'property', attributes['property'],
       if(mapContains(attributes,'content'),attributes['content'],NULL)
FROM elements WHERE tag='meta' AND mapContains(attributes,'property')
UNION ALL
SELECT document_id, node_index, 'meta', 'http-equiv', attributes['http-equiv'],
       if(mapContains(attributes,'content'),attributes['content'],NULL)
FROM elements WHERE tag='meta' AND mapContains(attributes,'http-equiv')
UNION ALL
SELECT document_id, node_index, 'meta', 'charset', 'charset', attributes['charset']
FROM elements WHERE tag='meta' AND mapContains(attributes,'charset')
UNION ALL
SELECT document_id, node_index, 'link', 'rel',
       arrayJoin(arrayDistinct(arrayFilter(token -> token != '',
           splitByRegexp('[\\t\\n\\f\\r ]+',attributes['rel'])))),
       if(mapContains(attributes,'href'),attributes['href'],NULL)
FROM elements WHERE tag='link' AND mapContains(attributes,'rel')
UNION ALL
SELECT document_id, node_index, 'html', 'lang', 'lang', attributes['lang']
FROM elements WHERE tag='html' AND mapContains(attributes,'lang');
