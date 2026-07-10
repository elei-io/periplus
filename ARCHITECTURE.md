# Atlas architecture

Atlas turns web pages into durable, queryable crawl records. The project deliberately has one
path from user intent to stored analytical state:

```text
API / CLI -> action -> crawl -> raw HTML + ingestion message -> repository writer -> DuckLake
                  \-> task run state in Postgres
```

## Ownership

- `backend/actions/` contains the user-facing search, index, crawl, calibrate, schema, and extract
  behavior. Actions compose other actions; API and CLI adapters stay thin.
- `backend/tasks/` owns editable schedules and the frozen run envelope. Postgres is control-plane
  state, not crawl history.
- `backend/repository/` is the only durable crawl-storage boundary. Crawl workers store
  content-addressed raw HTML and publish a frozen ingestion message. The repository writer is the
  only DuckLake writer.
- `backend/repository/ducklake/` is an implementation detail of the repository.
- `backend/dom/` owns the versioned DOM projection written into DuckLake.
- JetStream carries execution and ingestion work. Prometheus owns operational time series.

## Rules that keep this small

1. Crawl is the only page-acquisition primitive. Other actions call it.
2. Calibration is explicit. A missing policy uses the simple static transport; crawling never
   creates policy state as a side effect.
3. A task run freezes its primitive, input, policy snapshots, and schema references before queueing.
4. Crawl history never enters the control-plane Postgres database.
5. Workers never write DuckLake directly. There is one staged, batched ingestion path.
6. Raw HTML is content-addressed and immutable. DuckLake rows refer to repository object keys, not
   local paths.
7. Indexing is an in-memory bounded breadth-first walk. A larger distributed crawler should be a
   separately justified project, not hidden inside this primitive.
8. Add formats, services, queues, and abstractions only for an active caller.

## Runtime

The API validates requests and exposes records. The task worker claims scheduled runs, executes the
frozen action envelope, and reports progress. The repository writer consumes page ingestion jobs,
builds bounded DOM Parquet staging files, commits microbatches to DuckLake, then acknowledges the
message. Failed terminal ingestion is retained in a dead-letter stream and can be inspected or
requeued through `atlas repository`.

Docker Compose starts Postgres, runs Alembic, creates the DuckLake metadata database, bootstraps the
catalogue, starts NATS, then starts the API, task worker, and repository writer. Disk storage is the
default; MinIO is available through the `s3` profile.

The exact durable schema and failure invariants live in [STORAGE.md](STORAGE.md).
