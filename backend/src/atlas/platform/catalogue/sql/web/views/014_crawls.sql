CREATE OR REPLACE VIEW web.crawl AS
SELECT
    crawl_id,
    kind,
    graph_id,
    graph_config_hash,
    graph_config,
    root_url_count,
    started_at,
    finished_at,
    stop_reason
FROM ingest.crawls;
