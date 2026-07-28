CREATE OR REPLACE VIEW web.pages AS
SELECT
    page_id,
    normalized_url AS url,
    scheme,
    hostname,
    port,
    path,
    query,
    registrable_domain
FROM material.pages;
