# Atlas architecture

Atlas turns web pages into durable, queryable crawl records. The project deliberately has one
path from user intent to stored analytical state:

```text
API / CLI -> NATS task work -> action -> crawl -> raw HTML + ingestion message -> repository writer -> DuckLake
                 \-> current run state in NATS KV
```

## Ownership

- `backend/actions/` contains the user-facing search, index, crawl, calibrate, schema, and extract
  behavior. Actions compose other actions; API and CLI adapters stay thin.
- `backend/tasks/` owns editable schedules in Postgres and frozen run execution in JetStream/KV.
  Postgres is definition state, not execution state or crawl history.
- `backend/repository/` is the only durable crawl-storage boundary. Crawl workers store
  content-addressed raw HTML and publish a frozen ingestion message. The repository writer is the
  only DuckLake writer.
- `backend/repository/ducklake/` is an implementation detail of the repository.
- `backend/dom/` owns the versioned DOM projection written into DuckLake.
- JetStream carries task and ingestion work. NATS KV owns current task-run and worker-presence state.
  Prometheus owns operational time series.

## Rules that keep this small

1. Crawl is the only page-acquisition primitive. Other actions call it.
2. Calibration is explicit. A missing policy uses the simple static transport; crawling never
   creates policy state as a side effect.
3. A NATS task run freezes its primitive, input, policy snapshots, and schema references before queueing.
4. Crawl history never enters the control-plane Postgres database.
5. Workers never write DuckLake directly. There is one staged, batched ingestion path.
6. Raw HTML is content-addressed and immutable. DuckLake rows refer to repository object keys, not
   local paths.
7. Browser concurrency is bounded per worker; deployment replica count determines total capacity.
8. Indexing is an in-memory bounded breadth-first walk. A larger distributed crawler should be a
   separately justified project, not hidden inside this primitive.
9. Add formats, services, queues, and abstractions only for an active caller.

## Runtime

The API validates requests and publishes frozen runs. The task worker consumes JetStream work,
updates run state through KV compare-and-swap, and reports progress. The repository writer consumes page ingestion jobs,
builds bounded DOM Parquet staging files, commits microbatches to DuckLake, then acknowledges the
message. Failed terminal ingestion is retained in a dead-letter stream and can be inspected or
requeued through `atlas repository`.

Docker Compose starts Postgres, runs Alembic, creates the DuckLake metadata database, bootstraps the
catalogue, starts NATS, then starts the API, task worker, and repository writer. Disk storage is the
default; MinIO is available through the `s3` profile.

The exact durable schema and failure invariants live in [STORAGE.md](STORAGE.md).
