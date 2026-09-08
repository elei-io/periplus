CREATE OR REPLACE VIEW web.collection AS
SELECT definition.collection_id,
       definition.recorded_at AS requested_at,
       definition.specification,
       outcome.recorded_at AS settled_at,
       TRY_CAST(json_extract_string(definition.specification, '$.retention_seconds') AS BIGINT) AS retention_seconds,
       outcome.recorded_at + TRY_CAST(json_extract_string(definition.specification, '$.retention_seconds') AS BIGINT) * INTERVAL '1 second' AS expires_at,
       outcome.outcome,
       outcome.seed_provenance,
       outcome.consumed_pages,
       outcome.supplied_pages,
       outcome.failed_pages
FROM ingest.collections AS definition
LEFT JOIN ingest.collection_outcomes AS outcome
  ON outcome.collection_id = definition.collection_id;
