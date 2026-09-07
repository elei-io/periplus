CREATE OR REPLACE VIEW web.collection AS
SELECT definition.collection_id,
       definition.recorded_at AS requested_at,
       definition.specification,
       outcome.recorded_at AS settled_at,
       outcome.outcome,
       outcome.seed_provenance,
       outcome.consumed_pages,
       outcome.supplied_pages,
       outcome.failed_pages
FROM ingest.collections AS definition
LEFT JOIN ingest.collection_outcomes AS outcome
  ON outcome.collection_id = definition.collection_id AND outcome.visibility = 'public'
WHERE definition.visibility = 'public';
