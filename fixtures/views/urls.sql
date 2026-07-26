-- atlas:description=Canonical URL observations and provenance derived from retained crawls and page metadata.
CREATE VIEW views.urls AS
SELECT DISTINCT
    url,
    scheme,
    host,
    port,
    registrable_domain,
    path,
    query
FROM crawls;
