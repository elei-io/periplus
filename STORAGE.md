# Storage contract

This is the non-negotiable boundary for Atlas durable state.

## State owners

| Owner | Stores | Must not store |
| --- | --- | --- |
| Postgres `atlas` | tasks, schedules, frozen task-run envelopes, schemas, crawl policies | crawl history, HTML, DOM elements |
| Repository objects | immutable `html/sha256/...html.zst` objects | mutable metadata |
| DuckLake | documents, crawls, elements, run manifests, run-to-crawl usage | task scheduling state |
| JetStream | task execution, ingestion jobs/results, progress, dead letters | irreplaceable long-term analytics |
| Prometheus | operational metrics and history | correctness-critical state |

## Durable records

`documents` has one row per HTML SHA-256 identity. It records the raw object key, byte sizes,
compression, parser recipe, DOM schema version, and element count.

`crawls` has one row per acquisition attempt. It records task/run provenance, requested and final
URLs, timing/status, the frozen input hash, policy/schema references, warnings, errors, and an
optional document identity. A failed acquisition may be documentless but must contain an error.

`elements` is the bounded, versioned DOM projection keyed by document identity and element index.
DuckLake owns its physical Parquet layout and compaction; Atlas must not create a permanent file per
crawl in DuckLake's data directory.

`run_manifests` freezes task revision, primitive, input, and queue time. `run_crawl_usages` records
which crawls a run used, their role and order, and whether they were returned to the caller.

## Write path

1. Crawl acquires a page and computes the HTML SHA-256 identity.
2. It stores compressed HTML idempotently under the content-addressed object key.
3. It publishes a frozen ingestion job containing repository-relative identities and provenance.
4. The repository writer verifies the raw object, creates a bounded local DOM staging file, and
   commits a microbatch of document, crawl, element, manifest, and usage rows.
5. Only after commit does it acknowledge the message and remove staging.

Writes are idempotent. Reusing an identity with different immutable content is a conflict. A worker
crash may cause redelivery, never a second logical crawl row with conflicting values.

## Reads and cache

Actions resolve cached pages through the repository boundary. A hit requires a matching normalized
URL and input hash, acceptable age/quality, a present verified raw object, and a current DOM parser
recipe. Missing raw data is a miss. A stale projection may be rebuilt by the repository path.

Public point reads expose documents by content ID and crawls by UUID. Raw local paths and DuckLake
physical paths are never API contracts.

## Bounds and recovery

HTML bytes, element count, staged bytes, microbatch items/rows/bytes, NATS envelope size, task result
size, index pages, and index links all have explicit limits. Limit failures are terminal and clear;
they must not silently truncate durable state.

The writer periodically removes abandoned staging files. After configured redeliveries, terminal
jobs enter the durable dead-letter stream. Operators use `atlas repository dead-letters` and
`atlas repository requeue` for recovery. Object deletion and DuckLake compaction require an
explicit maintenance command; they are never hidden in a read or crawl request.

## Configuration

`ATLAS_REPOSITORY_STORAGE=disk|s3` selects both raw-object and DuckLake data storage.
`ATLAS_REPOSITORY_*` configures that storage. `ATLAS_CATALOGUE_*` configures DuckLake metadata and
its local DuckDB runtime. `ATLAS_INGEST_*` configures batching, delivery, results, and staging
cleanup. Do not introduce a second backend selector or a direct DuckLake write path.
