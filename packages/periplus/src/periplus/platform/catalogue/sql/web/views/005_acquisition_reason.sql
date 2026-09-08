CREATE OR REPLACE VIEW web.acquisition_reason AS
SELECT record_id AS reason_id,
       observation_id, collection_id, parent_observation_id,
       reason, policy_version, rule_id, recorded_at AS decided_at
FROM ingest.acquisition_reasons;
