CREATE OR REPLACE VIEW web.jsonld AS
SELECT
    content_sha256 AS content_id,
    element_index,
    type_terms,
    value
FROM material.jsonld_values;
