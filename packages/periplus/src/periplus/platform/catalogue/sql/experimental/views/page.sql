CREATE OR REPLACE VIEW experimental.page AS
SELECT page_url AS url FROM experimental.capture
UNION
SELECT effective_url AS url FROM experimental.capture WHERE effective_url IS NOT NULL
UNION
SELECT target_url AS url FROM experimental.link;
