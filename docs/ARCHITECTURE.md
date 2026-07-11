# Architecture

Atlas has one path from a requested action to durable analytical state:

```text
CLI / web
    |
FastAPI -- definitions --------------------------> Postgres
    |
    +-- frozen task run --> JetStream/KV --> runtime worker
                                              |
                                           action
                                              |
                                            crawl
                                              |
                 +----------------------------+-------------------+
                 |                                                |
       immutable HTML.zst                              frozen ingestion job
       filesystem or S3                                      JetStream
                                                                  |
                                                         repository worker
                                                                  |
                                                              DuckLake

Saved-query materializations add one supervised planner/evaluator beside this path. DuckLake CDC
discovers changed document scopes and historical activation scans page through bounded scope IDs.
Both publish JetStream work. Evaluation streams a bounded Arrow object through the configured
repository object store; only the repository worker verifies and commits it to DuckLake.
```

Prometheus receives operational metrics. It is not part of the correctness path.

## State ownership

| Owner | Authoritative state | Must not own |
| --- | --- | --- |
| Postgres `atlas` | Editable tasks, schedules, URL matches, crawl policies, and reusable schemas | Task execution, crawl history, HTML, DOM elements |
| NATS JetStream/KV | Work delivery, current run state, worker presence, progress, ingestion results, and dead letters | Irreplaceable long-term analytics |
| Repository objects | Immutable content-addressed raw HTML | Mutable metadata |
| DuckLake | Documents, crawl attempts, and versioned DOM elements | Scheduling or current execution state |
| Prometheus | Operational counters, gauges, and latency distributions | Correctness-critical state |

Postgres may retain UUID provenance for a NATS run, but that does not make the run a Postgres
entity. DuckLake may retain task and policy provenance, but it does not own their editable
definitions.

## Task execution

An API action request creates a frozen run envelope. A persisted task contributes its primitive,
revision, input, policy snapshots, and schema references before the run is queued. Workers consume
the small JetStream work message, claim current state with KV compare-and-swap, execute the frozen
input, and write bounded output back to KV.

Progress events are short-lived observations for clients. Worker presence is TTL-backed. Neither
is durable analytical history. Browser and task concurrency are local worker limits; adding worker
replicas adds deployment capacity.

## Crawl and ingestion

`crawl` is the only page-acquisition primitive. Search, index, schema generation, and extraction
compose it rather than creating their own browser paths.

For a captured page, the runtime worker:

1. Normalizes the request and acquires the page under a bounded local permit.
2. Hashes the raw UTF-8 HTML and stores compressed content idempotently under
   `raw/html/sha256/<prefix>/<hash>.html.zst`.
3. Publishes a frozen ingestion job containing repository-relative identity and provenance.
4. Waits for durable ingestion state; an inbox reply may reduce latency but is not authoritative.

The repository worker is the only runtime DuckLake writer. It verifies the raw object, builds a
bounded page-local DOM staging file, commits document/crawl/element microbatches, records the
result, acknowledges the message, and removes staging. Redelivery is safe because identities and
writes are idempotent.

Terminal ingestion failures enter a file-backed dead-letter stream. Explicit repository commands
inspect and requeue them. The repository worker runs bounded, threshold-driven DuckLake small-file
compaction between ingestion batches. Tiny DuckLake writes use the documented metadata inlining
limit and are flushed before compaction. Compaction output is bounded by DuckLake's
`max_compacted_files`; superseded files are reclaimed only after the configured read-safety grace
period. Snapshot expiration, orphan deletion, and other retention-changing maintenance remain
explicit operations, never hidden side effects of reads or crawls.

Repository and materialization transactions annotate their DuckLake snapshots with an Atlas author,
operation description, and bounded identifiers. Write responses use DuckLake's
`last_committed_snapshot()` for the current connection rather than the globally latest snapshot, so
concurrent writers cannot misattribute a commit.

## Reads and cache

Actions read through the repository boundary. A cache hit requires matching normalized URL and
input hash, acceptable age and quality, a verified raw object, and a current DOM parser recipe.
Missing evidence is a miss. A stale structural projection can be rebuilt from raw HTML through the
same ingestion path.

Public point reads use document content IDs and crawl UUIDs. Local paths and DuckLake physical
Parquet paths are implementation details.

## Bounds

HTML size, DOM element count, staging bytes, ingestion batch size, NATS envelopes, task results,
index breadth, and concurrent browser work have explicit limits. Limit failures must be clear and
terminal; durable state must not be silently truncated.

Index is a bounded in-memory breadth-first walk. A distributed frontier is a separate project that
requires evidence that the current primitive is insufficient.

## Code ownership

- `backend/actions/` owns user-facing behavior and action composition.
- `backend/control/` owns editable Postgres-backed definitions.
- `backend/runtime/` owns NATS-backed execution and process-local coordination.
- `backend/repository/objects/` owns immutable raw storage.
- `backend/repository/ingestion/` owns the repository queue and single-writer path.
- `backend/repository/catalogue/` implements DuckLake behind the repository boundary.
- `backend/materialization/` owns scoped planning, CDC discovery, bounded evaluation, durable
  failure administration, and the repository-writer commit contract.
- `backend/repository/service.py` is the application-facing durable read/write boundary.
- `backend/dom/` owns the versioned structural projection.
- `backend/api/` and `backend/cli/` adapt external requests and remain thin.
- `backend/db/` owns Postgres infrastructure and migrations.

Docker Compose runs one idempotent `atlas-setup` job after Postgres is healthy. It creates the
separate catalogue database, applies Alembic migrations, and bootstraps DuckLake before the API or
workers start. Disk is the default repository backend; the optional S3 profile uses a separate
MinIO bucket-initialization job because external S3 provisioning is operator-owned.

## Exact contracts

Architecture documents ownership and invariants. Code remains authoritative for exact shapes:

- Configuration and defaults: [`.env.example`](../.env.example)
- Postgres models: [`backend/control/`](../backend/control/)
- Postgres migrations: [`backend/db/alembic/`](../backend/db/alembic/)
- Task work and KV state: [`backend/runtime/task_queue.py`](../backend/runtime/task_queue.py) and
  [`backend/runtime/task_runs.py`](../backend/runtime/task_runs.py)
- Repository messages and state:
  [`backend/repository/ingestion/queue.py`](../backend/repository/ingestion/queue.py)
- DuckLake tables:
  [`backend/repository/catalogue/schema.py`](../backend/repository/catalogue/schema.py)
- DOM projection: [`backend/dom/`](../backend/dom/)
- HTTP surface: the FastAPI-generated OpenAPI document
