CREATE OR REPLACE VIEW web.page AS
SELECT
    page.page_id,
    page.normalized_url AS url,
    page.scheme,
    page.hostname,
    page.port,
    page.path,
    page.query,
    page.registrable_domain
FROM material.pages AS page;
