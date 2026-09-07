CREATE OR REPLACE VIEW web.fulfillment AS
SELECT record_id AS fulfillment_id,
       collection_id, observation_id, requested_url, parent_observation_id,
       depth, rule_id, mode, recorded_at AS decided_at
FROM ingest.fulfillments
WHERE visibility = 'public';
