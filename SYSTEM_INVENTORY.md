# Atlas storage, messaging, and environment inventory

This inventory describes the current repository at migration head `20260711_0029`. Historical
tables removed by migrations (`artifacts`, `task_effects`, `effect_runs`, `task_runs`,
`task_run_leases`, `worker_heartbeats`, and `crawl_permits`) are not current tables.
DuckLake's own extension-managed metadata tables are an implementation detail of `ducklake-client`;
the three logical Atlas DuckLake tables below are the repository contract.

## PostgreSQL control-plane tables

### `tasks`

Purpose: editable, schedulable action definitions. A task is Postgres configuration; its executions
live in NATS.

- `id` — UUID primary key.
- `name` — human-readable task name.
- `primitive` — action to execute: search, index, crawl, schema, extract, or calibrate.
- `input_json` — editable primitive-specific input.
- `revision` — definition revision frozen into each new run.
- `schedule_json` — optional once/cron/interval schedule.
- `identity_key` — optional unique caller-stable identity used for idempotent definition lookup.
- `archived_at` — when the task was removed from active scheduling; null means active.
- `archived_reason` — operator-facing explanation for archival.
- `last_run_at` — most recent queue/run time used by scheduling and display.
- `next_run_at` — next calculated schedule occurrence.
- `created_at` — creation timestamp.
- `updated_at` — last definition update timestamp.

### `url_matches`

Purpose: normalized reusable URL matching definitions shared by query schemas and crawl policies.

- `id` — UUID primary key.
- `scheme` — URL scheme to match.
- `host` — normalized host to match.
- `domain` — registrable/logical domain used for grouping and lookup.
- `path_pattern` — path matcher value.
- `match_type` — interpretation of the path pattern, such as exact or pattern matching.
- `query_policy` — whether/how query parameters participate in identity.
- `enabled` — whether matching is active.
- `priority` — precedence among matching definitions.
- `created_by_task_run_id` — optional provenance run that created the matcher.
- `updated_by_task_run_id` — optional provenance run that last updated it.
- `created_at` — creation timestamp.
- `updated_at` — last update timestamp.

### `crawl_policies`

Purpose: explicit, user-editable page acquisition policy selected by URL matching.

- `id` — UUID primary key.
- `metric_slug` — unique low-cardinality policy label for metrics.
- `domain_group` — operator grouping label.
- `url_match_id` — optional structured matcher; set null if the matcher is deleted.
- `match` — legacy/direct URL pattern used to select the policy.
- `enabled` — whether the policy participates in selection.
- `config` — browser mode, wait strategy, concurrency, cache-block rules, and run overrides.
- `revision` — revision frozen into task runs and crawl provenance.
- `created_at` — creation timestamp.
- `updated_at` — last update timestamp.

### `data_schemas`

Purpose: durable reusable extraction schemas generated for matching pages.

- `id` — UUID primary key.
- `identity_key` — unique stable schema identity.
- `match` — URL pattern controlling reuse.
- `enabled` — whether automatic selection may use it.
- `priority` — selection precedence.
- `prompt` — generation/extraction intent supplied by the user.
- `prompt_hash` — stable hash for prompt identity and reuse.
- `schema_type` — schema representation/extractor type.
- `target_json_hash` — optional hash of an example target payload.
- `domain` — optional normalized domain metadata.
- `path` — optional normalized path metadata.
- `schema_json` — generated extraction schema.
- `schema_hash` — content hash of `schema_json`.
- `generated_from_crawl_id` — DuckLake crawl used as generation evidence.
- `generated_from_document_id` — content-addressed document used as evidence.
- `generated_by_task_run_id` — optional NATS run UUID retained as provenance without a Postgres foreign key.
- `inputs_json` — frozen generation inputs and provenance details.
- `validation_status` — latest schema validation state.
- `failure_count` — observed extraction/validation failures.
- `last_failed_at` — most recent failure time.
- `last_error` — latest failure explanation.
- `warnings_json` — bounded warning summary.
- `created_at` — creation timestamp.
- `updated_at` — last update timestamp.

### `query_schemas`

Purpose: durable description of URL query/form parameters inferred from a page.

- `id` — UUID primary key.
- `identity_key` — unique stable schema identity.
- `url_match_id` — optional shared structured URL matcher.
- `match` — direct URL pattern controlling reuse.
- `enabled` — whether automatic selection may use it.
- `priority` — selection precedence.
- `schema_type` — query-schema representation type.
- `domain` — optional normalized domain metadata.
- `path` — optional normalized path metadata.
- `schema_json` — generated JSON schema for query parameters.
- `params_json` — normalized parameter definitions.
- `evidence_json` — forms, controls, and other evidence supporting inference.
- `schema_hash` — content hash for change/reuse detection.
- `generated_from_crawl_id` — DuckLake crawl used as evidence.
- `generated_from_document_id` — content-addressed document used as evidence.
- `generated_by_task_run_id` — optional generating run.
- `inputs_json` — frozen generation inputs/provenance.
- `warnings_json` — bounded warning summary.
- `created_at` — creation timestamp.
- `updated_at` — last update timestamp.

### `alembic_version`

Purpose: Alembic-owned migration-head marker, not application data.

- `version_num` — currently applied revision, presently `20260711_0028`.

## DuckLake analytical tables

### `documents`

Purpose: one immutable logical document per raw HTML SHA-256 identity and its current DOM recipe.

- `document_id` — canonical `sha256:<hex>` identity.
- `html_sha256` — raw 64-character SHA-256 digest.
- `html_object_key` — repository-relative compressed HTML object key.
- `html_content_type` — stored media type.
- `html_encoding` — character encoding used to interpret raw HTML.
- `html_size_bytes` — uncompressed byte count.
- `html_compressed_size_bytes` — repository object byte count.
- `compression` — object compression algorithm, currently zstd.
- `dom_schema_version` — durable element schema version.
- `parser_name` — DOM parser implementation.
- `parser_version` — parser version used for this projection.
- `parser_options_hash` — hash of parser options affecting output.
- `element_count` — declared number of projected DOM elements.
- `created_at` — first durable document creation time.

### `crawls`

Purpose: immutable acquisition history. A crawl records an attempt even when acquisition failed.

- `crawl_id` — retry-stable UUID identity.
- `document_id` — resulting document identity; null only for a recorded acquisition failure.
- `run_id` — originating frozen task run.
- `task_id` — originating task definition.
- `task_revision` — task revision used by the run.
- `primitive` — action context that requested acquisition.
- `requested_url` — caller-requested URL.
- `normalized_url` — canonical URL used for cache lookup.
- `final_url` — final URL after redirects, if known.
- `captured_at` — acquisition timestamp.
- `status_code` — HTTP response status, if available.
- `duration_ms` — acquisition duration in milliseconds.
- `input_json` — frozen acquisition-relevant input.
- `input_hash` — cache identity hash of that input.
- `crawl_policy_id` — selected policy identity, if any.
- `crawl_policy_revision` — selected policy revision.
- `data_schema_id` — data schema involved in the operation, if any.
- `query_schema_id` — query schema involved in the operation, if any.
- `warnings_json` — page quality/acquisition warnings.
- `errors_json` — acquisition errors; required for documentless rows.

### `elements`

Purpose: versioned structural DOM projection shared by all crawls of the same document.

- `document_id` — owning document identity.
- `element_index` — page-local stable preorder element index.
- `parent_index` — parent element index; null for the root.
- `tag` — element tag/local name.
- `namespace_uri` — optional XML/HTML namespace URI.
- `attributes` — map of attribute names to values.
- `text` — element text before children.
- `tail` — text following the element in its parent.

## NATS JetStream streams and messages

### Stream `ATLAS_TASKS`, subject `atlas.tasks.run`

Purpose: durable work queue for task executions. Each `TaskWork` message has one field:

- `run_id` — UUID key of the frozen state in `ATLAS_TASK_RUNS`.

The durable consumer is `atlas-task-workers`; explicit acknowledgement completes terminal work and
negative acknowledgement retries a non-terminal failed attempt.

### Stream `ATLAS_PROGRESS`, subject `atlas.task-runs.<run_id>.progress`

Purpose: short-lived, best-effort task progress and terminal notifications used by SSE/CLI. Postgres
remains authoritative. The stream uses file storage, limits retention, and `NATS_RETENTION` max age.

Message envelope fields:

- `event_id` — `<attempt>:<sequence>` resume cursor.
- `run_id` — task-run UUID as text.
- `attempt` — run attempt that emitted the event.
- `sequence` — monotonically increasing sequence within that attempt.
- `type` — `progress` or a lifecycle status such as succeeded/failed/cancelled/skipped.
- `timestamp` — UTC emission timestamp.
- `data` — event-specific payload. Progress data uses the fields below; terminal data contains `status` and `error`.

Progress `data` fields:

- `phase` — named operation phase such as crawl, cache, extract, or persist_result.
- `status` — waiting, started, succeeded, or failed.
- `resource` — optional URL/provider/resource being processed.
- `current` — optional completed/current count.
- `total` — optional known total count.
- `message` — optional human-readable update.
- `metadata` — phase-specific bounded structured details.
- `duration` — optional elapsed seconds for a completed phase.
- `error` — optional failure text.
- `operation_id` — identity pairing start and terminal events for concurrent operations.

### Stream `ATLAS_REPOSITORY`, subject `atlas.repository.ingest`

Purpose: file-backed work queue consumed only by the repository writer. Messages are acknowledged
after DuckLake commit or after terminal failure has been durably recorded.

`IngestionJob` fields:

- `request_id` — retry-stable idempotency key and KV key.
- `reply_subject` — ephemeral Core NATS inbox used only to wake the producer early.
- `enqueued_at` — operation enqueue time.
- `crawl` — nested `CrawlRecord`; fields exactly match DuckLake `crawls` above.

The non-durable reply inbox carries `IngestionResponse`:

- `request_id` — operation identity.
- `result` — optional `CatalogueWriteResult`.
- `error` — optional failure text.

`CatalogueWriteResult` fields are `document_id`, `crawl_id`, `document_created`, `crawl_created`, and
`repository_snapshot`.

### Stream `ATLAS_REPOSITORY_DEAD_LETTER`, subject `atlas.repository.dead_letter`

Purpose: limits-retention, file-backed operator record for terminal repository ingestion failures.
Messages are zstd-compressed JSON and remain until explicitly requeued/deleted or stream limits act.

`DeadLetterEntry` fields:

- `job` — complete frozen `IngestionJob`, with all fields listed above.
- `error` — terminal failure explanation.
- `failed_at` — UTC terminal-failure timestamp.
- `delivery_count` — number of work-queue deliveries attempted.

## NATS KV storage

### Bucket `ATLAS_TASK_RUNS`

Purpose: authoritative current task-run state and frozen execution envelope. Run keys are UUID hex;
`active-<task_uuid_hex>` keys atomically enforce at most one queued/running run per task.
`TaskRunState` fields:

- `id`, `task_id`, `task_revision`, `primitive`, `trigger_kind` — frozen run identity/definition.
- `status` — queued, running, succeeded, failed, cancelled, or skipped.
- `data_schema_id`, `input_json`, `crawl_policy_snapshots_json` — frozen execution inputs.
- `queued_at`, `started_at`, `finished_at`, `cancellation_requested_at`, `cancelled_at` — lifecycle times.
- `attempt`, `failed_attempts`, `max_attempts` — retry state.
- `output_json`, `warnings_json`, `error` — bounded terminal/current outcome.
- `worker_id`, `execution_token` — current worker and compare-and-swap ownership generation.
- `created_at`, `updated_at` — state timestamps.

### Bucket `ATLAS_TASK_WORKERS`

Purpose: TTL-backed ephemeral worker presence. Key is sanitized worker ID. `WorkerState` fields:

- `worker_id`, `started_at`, `last_seen_at` — identity and liveness.
- `capacity`, `active_run_count` — local configured and used task slots.
- `stopping`, `version` — shutdown and deployment metadata.

### Bucket `ATLAS_REPOSITORY_RESULTS`

Purpose: durable latest state for each retry-stable repository operation. Key is `request_id`; value
is an `IngestionState`. History is 1, storage is file-backed, and TTL/size/replicas are configurable.

`IngestionState` fields:

- `request_id` — KV key repeated inside the value for validation.
- `status` — pending, succeeded, or failed.
- `crawl` — nested crawl record.
- `enqueued_at` — operation enqueue/reset time.
- `updated_at` — latest state transition time.
- `published_at` — time JetStream accepted the work message; null means publication must resume.
- `result` — terminal `CatalogueWriteResult` on success.
- `error` — terminal error text on failure.

Key forms are `crawl-<crawl_uuid_hex>` and `projection-<recipe_hash>`.

## Environment variables

The list includes runtime, Compose, migration, CLI, and opt-in test variables found in the repo.
Blank optional values generally mean “use the library/default behavior.”

### Database and local Compose

- `DATABASE_URL` — SQLAlchemy URL for the Atlas control-plane Postgres database.
- `POSTGRES_DB` — Compose primary Atlas database name.
- `POSTGRES_USER` — Compose Postgres user and catalogue database owner.
- `POSTGRES_PASSWORD` — Compose Postgres password.
- `POSTGRES_PORT` — host port mapped to Compose Postgres.
- `POSTGRES_CATALOGUE_DB` — separate Postgres database holding DuckLake metadata.
- `PGHOST` — standard libpq host supplied to the Compose database-initialization job.
- `PGPASSWORD` — standard libpq password supplied to the Compose database-initialization job.
- `MINIO_ROOT_USER` — local S3-profile MinIO administrator/access key.
- `MINIO_ROOT_PASSWORD` — local S3-profile MinIO administrator/secret key.
- `ATLAS_ALLOW_LEGACY_STORAGE_DROP` — explicit one-time acknowledgement allowing the historical destructive crawl-table migration.

### DuckLake catalogue/runtime

- `ATLAS_CATALOGUE_CATALOG` — metadata backend: postgres, duckdb, or sqlite.
- `ATLAS_CATALOGUE_CATALOG_DSN` — required Postgres metadata DSN when catalog is postgres.
- `ATLAS_CATALOGUE_CATALOG_PATH` — local DuckDB/SQLite metadata file when using those catalog types.
- `ATLAS_CATALOGUE_ROOT` — fallback root for local catalogue metadata and storage.
- `ATLAS_CATALOGUE_ALIAS` — DuckDB attached catalogue alias, default `atlas`.
- `ATLAS_CATALOGUE_SCHEMA` — logical DuckLake schema, default `main`.
- `ATLAS_CATALOGUE_DATA_INLINING_ROW_LIMIT` — DuckLake attachment threshold for metadata data inlining.
- `ATLAS_CATALOGUE_OVERRIDE_DATA_PATH` — whether attachment overrides the catalogue's recorded data path.
- `ATLAS_CATALOGUE_DUCKDB_DATABASE` — process-local DuckDB database, normally `:memory:`.
- `ATLAS_CATALOGUE_DUCKDB_THREADS` — optional DuckDB thread count.
- `ATLAS_CATALOGUE_DUCKDB_MEMORY_LIMIT` — optional DuckDB memory limit string.
- `ATLAS_CATALOGUE_DUCKDB_MAX_TEMP_SIZE` — optional DuckDB temporary-directory size limit.
- `ATLAS_CATALOGUE_DUCKDB_TEMP_DIRECTORY` — optional DuckDB spill directory.

### Raw repository and S3

- `ATLAS_REPOSITORY_STORAGE` — shared raw-object/DuckLake data backend: disk or s3.
- `ATLAS_REPOSITORY_ROOT` — disk repository root; raw objects, lake data, and default staging derive from it.
- `ATLAS_REPOSITORY_STAGING_ROOT` — explicit local temporary DOM Parquet staging directory.
- `ATLAS_REPOSITORY_S3_BUCKET` — S3 bucket used by raw objects and DuckLake data.
- `ATLAS_REPOSITORY_S3_PREFIX` — optional common key prefix; Atlas adds raw/lake subpaths.
- `ATLAS_REPOSITORY_S3_ENDPOINT` — optional custom S3/MinIO endpoint.
- `ATLAS_REPOSITORY_S3_REGION` — repository-specific S3 region.
- `ATLAS_REPOSITORY_S3_KEY_ID` — repository-specific access key.
- `ATLAS_REPOSITORY_S3_SECRET_ACCESS_KEY` — repository-specific secret key.
- `ATLAS_REPOSITORY_S3_SESSION_TOKEN` — optional repository-specific temporary credential token.
- `ATLAS_REPOSITORY_S3_URL_STYLE` — S3 addressing style: auto, path, or virtual.
- `ATLAS_REPOSITORY_S3_USE_SSL` — explicit boolean controlling S3 HTTPS.
- `AWS_REGION` — standard fallback region when repository-specific region is absent.
- `AWS_ACCESS_KEY_ID` — standard fallback access key.
- `AWS_SECRET_ACCESS_KEY` — standard fallback secret key.
- `AWS_SESSION_TOKEN` — standard fallback temporary credential token.
- `ATLAS_REPOSITORY_MAX_HTML_BYTES` — hard uncompressed HTML size per document.
- `ATLAS_REPOSITORY_MAX_DOCUMENT_ELEMENTS` — hard DOM element count per document.
- `ATLAS_REPOSITORY_MAX_DOCUMENT_STAGED_BYTES` — hard page-local DOM staging byte size.

### Repository ingestion and NATS

- `NATS_URL` — NATS server URL used for progress and repository ingestion.
- `NATS_RETENTION` — max age for ephemeral progress events, e.g. `5m`.
- `ATLAS_NATS_MAX_ENVELOPE_BYTES` — application envelope ceiling kept below NATS `max_payload`.
- `ATLAS_INGEST_BATCH_ITEMS` — maximum jobs in one DuckLake microbatch.
- `ATLAS_INGEST_BATCH_ELEMENT_ROWS` — maximum projected element rows in a microbatch.
- `ATLAS_INGEST_BATCH_BYTES` — maximum staged bytes in a microbatch.
- `ATLAS_INGEST_BATCH_WAIT_SECONDS` — maximum wait used to accumulate a microbatch.
- `ATLAS_INGEST_ACK_WAIT_SECONDS` — JetStream consumer acknowledgement deadline.
- `ATLAS_INGEST_MAX_ACK_PENDING` — maximum unacknowledged writer messages.
- `ATLAS_INGEST_MAX_DELIVER` — application terminal-failure delivery budget before dead-lettering.
- `ATLAS_INGEST_STREAM_REPLICAS` — repository work/dead-letter stream replica count.
- `ATLAS_INGEST_RESULT_POLL_SECONDS` — producer KV polling interval while awaiting commit.
- `ATLAS_INGEST_RESULT_TTL_SECONDS` — ingestion-state KV retention.
- `ATLAS_INGEST_RESULT_MAX_BYTES` — ingestion-state KV bucket byte ceiling.
- `ATLAS_INGEST_RESULT_REPLICAS` — ingestion-state KV replica count.
- `ATLAS_INGEST_STAGING_CLEANUP_INTERVAL_SECONDS` — abandoned staging cleanup cadence.
- `ATLAS_INGEST_STAGING_GRACE_SECONDS` — minimum staging age before cleanup.

### Task worker, scheduling, crawling, and index

- `ATLAS_WORKER_ID` — explicit worker identity; empty derives hostname/PID.
- `ATLAS_WORKER_CONCURRENCY` — concurrent isolated task-run slots per worker.
- `ATLAS_WORKER_POLL_SECONDS` — fallback database polling cadence.
- `ATLAS_TASK_RUN_TIMEOUT_SECONDS` — maximum wall time for one isolated run.
- `ATLAS_TASK_RESULT_MAX_BYTES` — maximum serialized NATS run-state result size.
- `ATLAS_SCHEDULER_BATCH_SIZE` — maximum due tasks queued per scheduler pass.
- `ATLAS_CRAWL_CONCURRENCY_PER_RUN` — maximum concurrently acquired pages in one run.
- `ATLAS_BROWSER_CONCURRENCY` — browser capacity per worker; replicas determine total capacity.
- `ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS` — maximum wait to obtain crawl capacity.
- `ATLAS_CACHE_MAX_AGE_SECONDS` — default maximum age for a normal cache hit.
- `ATLAS_CACHE_STALE_IF_ERROR_SECONDS` — optional total age allowed as network-error fallback.
- `ATLAS_INDEX_PAGE_BATCH_SIZE` — URLs sent to crawl in each breadth-first batch.
- `ATLAS_INDEX_RESULT_LIMIT` — maximum detailed links embedded in an index action result.
- `MAX_EXTRACT_ATTEMPTS` — extraction retry/repair attempt count.
- `ATLAS_TASK_STREAM_REPLICAS` — task work stream and KV replica count.
- `ATLAS_TASK_ACK_WAIT_SECONDS` — task consumer acknowledgement deadline.
- `ATLAS_TASK_RUN_STATE_MAX_BYTES` — task-run KV bucket byte ceiling.
- `ATLAS_WORKER_PRESENCE_TTL_SECONDS` — worker presence KV expiry.

### Logging, metrics, and repository-writer health

- `ATLAS_LOG_FORMAT` — text or JSON worker logging.
- `ATLAS_LOG_LEVEL` — Python log threshold.
- `ATLAS_METRICS_ENABLED` — enable worker/repository Prometheus servers.
- `ATLAS_METRICS_HOST` — task-worker metrics bind host.
- `ATLAS_METRICS_PORT` — task-worker metrics port.
- `ATLAS_INGESTOR_METRICS_PORT` — repository-writer metrics port.
- `ATLAS_INGESTOR_HEALTH_HOST` — repository-writer health server bind host.
- `ATLAS_INGESTOR_HEALTH_PORT` — repository-writer health server port.
- `ATLAS_INGESTOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS` — event-loop heartbeat staleness threshold.
- `ATLAS_INGESTOR_HEALTH_PROBE_INTERVAL_SECONDS` — dependency probe cadence.
- `ATLAS_INGESTOR_HEALTH_PROBE_TIMEOUT_SECONDS` — timeout for catalogue/NATS health probes.

### LLM and external action configuration

- `OPENROUTER_API_KEY` — credential for schema/query generation and LLM extraction.
- `LLM_PROVIDER` — default OpenRouter model for generic LLM extraction.
- `OPENROUTER_SCHEMA_MODEL` — model used for data-schema generation.
- `OPENROUTER_SEARCH_EXTRACTOR_MODEL` — model used to interpret search/extraction content where configured.
- `OPENROUTER_QUERY_PARAM_MODEL` — model used for query-parameter schema generation.

### CLI and tests

- `ATLAS_API_URL` — CLI API base URL override; otherwise stored by `atlas init`.
- `ATLAS_TEST_DATABASE_URL` — dedicated Postgres admin URL enabling destructive/transactional integration tests.
- `ATLAS_TEST_MINIO` — set to `1` to enable opt-in MinIO repository integration tests.
- `ATLAS_TEST_MINIO_ENDPOINT` — endpoint used by those MinIO tests.

### Image build/runtime plumbing

- `UV_PROJECT_ENVIRONMENT` — build-step override placing the uv virtual environment at `/opt/atlas-venv`.
- `PATH` — Docker image runtime search path, prefixed with `/opt/atlas-venv/bin`.
