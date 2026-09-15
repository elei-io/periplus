-- Installed only by periplus-setup. Runtime workers never repair schemas.
CREATE DATABASE IF NOT EXISTS ingest;

CREATE TABLE IF NOT EXISTS ingest.visits
(
    evidence_sha256 FixedString(32),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),
    visit_id UUID,
    requested_url String CODEC(ZSTD(3)),
    effective_url Nullable(String) CODEC(ZSTD(3)),
    admitted_at DateTime64(6, 'UTC'),
    started_at Nullable(DateTime64(6, 'UTC')),
    observed_at Nullable(DateTime64(6, 'UTC')),
    finished_at DateTime64(6, 'UTC'),
    outcome LowCardinality(String),
    status_code Nullable(UInt16),
    capture_policy String CODEC(ZSTD(3)),

    -- Zero or one retained document, carried in the same row as its visit.
    document_id Nullable(UUID),
    document_attempt_id Nullable(UUID),
    document_observed_at Nullable(DateTime64(6, 'UTC')),
    representation LowCardinality(Nullable(String)),
    declared_media_type LowCardinality(Nullable(String)),
    detected_media_type LowCardinality(Nullable(String)),
    charset LowCardinality(Nullable(String)),
    content_sha256 Nullable(FixedString(32)),
    content_bytes Nullable(UInt64),
    object_key Nullable(String) CODEC(ZSTD(3)),
    storage_encoding LowCardinality(Nullable(String)),
    stored_bytes Nullable(UInt64),

    -- Ordered, bounded children of the immutable visit envelope.
    attempts Array(Tuple(
        attempt_id UUID,
        attempt_index UInt32,
        resource_usage Nullable(String),
        started_at DateTime64(6, 'UTC'),
        finished_at Nullable(DateTime64(6, 'UTC')),
        effective_url Nullable(String),
        status_code Nullable(UInt16),
        outcome String,
        failure_stage Nullable(String),
        failure_code Nullable(String),
        failure_message Nullable(String),
        steps Array(Tuple(
            step_index UInt32,
            action String,
            parameters String,
            started_at DateTime64(6, 'UTC'),
            duration_ms UInt64,
            outcome String,
            stopping_reason Nullable(String),
            error_code Nullable(String),
            error_message Nullable(String)
        ))
    )) CODEC(ZSTD(3)),

    PROJECTION by_visit
    (
        SELECT visit_id, _part_offset ORDER BY visit_id
    )
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(finished_at)
ORDER BY (requested_url, finished_at, visit_id);

CREATE TABLE IF NOT EXISTS ingest.fulfillments
(
    evidence_sha256 FixedString(32),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),
    record_id UUID,
    recorded_at DateTime64(6, 'UTC'),
    collection_id UUID,
    observation_id UUID,
    requested_url String CODEC(ZSTD(3)),
    parent_observation_id Nullable(UUID),
    depth UInt32,
    rule_id String,
    mode LowCardinality(String),

    PROJECTION by_observation
    (
        SELECT observation_id, _part_offset ORDER BY observation_id
    )
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(recorded_at)
ORDER BY (collection_id, requested_url, record_id);

CREATE TABLE IF NOT EXISTS ingest.acquisition_reasons
(
    evidence_sha256 FixedString(32),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),
    record_id UUID,
    recorded_at DateTime64(6, 'UTC'),
    observation_id UUID,
    collection_id UUID,
    parent_observation_id Nullable(UUID),
    reason LowCardinality(String),
    policy_version String,
    rule_id String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(recorded_at)
ORDER BY (observation_id, collection_id, record_id);
