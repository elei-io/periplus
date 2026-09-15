# Persistence schemas

These are the current contracts. Python models and the versioned SQL resources
are the executable definitions; internal build IDs and parser recipes are not
part of the public corpus API.

## Raw archive

`ingestion/captures.py` defines a frozen `Capture`:

```text
capture_id: UUID
requested_url, effective_url?
captured_at?, timestamp_precision: microsecond | second | unknown
http_status?, completeness
payload?: content_id (SHA-256), byte_length, object_key, stored_bytes,
          storage_encoding, representation, media_type, declared_media_type?, charset?
source?: provider, dataset, record_id, source WARC location/digests/headers
```

`content_id` identifies exact decoded payload bytes. `document_id` also includes
the interpretation (representation, media type and charset), so equal bytes
cannot silently share incompatible parsed output. Native rendered HTML and an
archived HTTP response body are distinct representations.

The repository contains content-addressed payloads plus `raw/corpus/v1/`:

| Path | Meaning |
| --- | --- |
| `journal/<shard>/<range>/<segment>.json.zst` | Immutable batch of 1–64 complete capture/retirement records; 16 independent journals |
| `retired/<prefix>/<capture-id>.json.zst` | Permanent corpus tombstone |
| `manifests/<sha256>.json.zst` | Sixteen journal heads, recipe, software object key and public schema version |
| `software/<sha256>.tar` | Source, SQL, dependency lock and runtime information |

A manifest is a small cut of the journal, not a copy of all captures. Object
keys are repository-relative. No customer IDs, collections, policies, budgets,
worker ownership or NATS sequence numbers are needed to interpret the archive.

A segment is bounded to 8 MiB expanded JSON; one capture is bounded to 2 MiB.
Its header records the shard, segment number, first logical event sequence,
publication time and record digest. Logical event sequences remain contiguous
across variable-size segments. `range = (segment - 1) // 4096` bounds files per
leaf directory. There are no per-capture envelope or commit-receipt objects.

An `ArchiveRef` names shard, logical sequence, segment, record offset, capture ID
and evidence digest. `archive_record_key` in material rows is the segment key
plus `#<record-offset>`; readers validate the capture identity and digest.
Publisher processes maintain an expendable disk-backed SQLite identity lookup,
replayed from metadata. It stores no authoritative facts and is unnecessary for
direct-reference reads or archive-only sequential verification. See [STORAGE.md](STORAGE.md).

## Postgres

`control` groups customer intent, results and editable policies. `state` groups
execution, retries, checkpoints, publication and short-lived diagnostics. No
`staging` schema or Postgres copy of derived material is installed. Workers write
bounded, verified batches directly to ClickHouse.

These are ownership boundaries, not instructions to truncate `state`: pending
outbox evidence, import accounting and retirement work must complete under their
existing lifecycle rules. `state.frontier_control` intentionally keeps its settings
and counters under one row lock; splitting that lock is a separate crawler change.


| Tables | Durable meaning |
| --- | --- |
| `control.collections` | Requested selection, budgets, status, outcome and progress |
| `control.collection_results` | Interest/result ID, collection ID, capture ID, requested URL, mode, outcome, selection context, recorded/archived timestamps and archive position |
| `control.request_definitions`, `control.request_schedules` | Editable recurring intent |
| `control.domain_policies`, `control.content_policies` | Editable crawl policies |
| `state.frontier_control` | One frontier lock row: live counts, dispatch settings, pause and cleanup cursors |
| `state.frontier_acquisitions`, `state.frontier_interests` | Bounded current shared frontier and per-collection execution |
| `state.frontier_outbox` | Frozen operational capture record until its archive publication is confirmed |
| `state.archive_imports` | Operator import request, source checkpoints, cancellation, retries and accounting |
| `state.material_builds` | Build/recipe/target IDs, phase, pause, blocker, manifest and verification cut |
| `state.material_build_ranges` | Sixteen historical and live cursor pairs per build |
| `state.material_batches` | Outstanding bounded batches, lease owner, attempt and error; deleted on completion |
| `state.material_publications` | `public_v1 -> serving build`, revision |
| `state.write_claims` | Expiring exact capture/body write exclusion |
| `state.capture_retirements` | Requested/completed archive tombstone work and errors |
| `control.public_access` | Product access settings |
| `state.query_executions` | Private query execution history, retained for 30 days |

`control.collection_results` replaces both analytical fulfillments and acquisition-reason
mirrors. It describes the business relationship to a capture. The frozen dispatch
participants remain temporary operational context on the shared acquisition.
Janitor cleanup removes completed frontier execution without deleting the
collection or its results.

A new Postgres instance can be initialized from the raw archive. This recreates
build execution and publication, not old customer records, permissions, billing
context, schedules or private query history. Those require the Postgres backup.

## NATS

| Contract | Contents and recovery |
| --- | --- |
| Frontier stream | Small deterministic acquisition/selection deliveries from the Postgres outbox |
| `PERIPLUS_CORPUS_EVENTS` | Small archive position hints; file-backed, bounded to one day; loss is harmless to corpus completeness |
| `PERIPLUS_MATERIAL_WORK` | Outstanding batch UUIDs on `periplus.material.batch.<recipe>`; file-backed work queue, no age expiry, reject new writes when full |
| `materializers_<recipe>` consumers | Explicit ACK after durable material/checkpoint handling; symmetric workers per recipe |
| Operation lease KV | Planner/import ownership; expiring coordination |
| Domain pacing KV | Per-domain website permits and timing |
| Crawler presence KV | Short-lived worker health/activity |

NATS stores neither HTML nor the permanent ingestion ledger. Raw journal heads
recover missed hints; Postgres recreates batch/frontier deliveries. A fresh NATS
instance is sufficient for archive-only recovery.

## ClickHouse

Each build owns one material database and one public-view database:

```text
material[_<build>].captures
  capture_id UUID (sort key), evidence_digest,
  requested_url, effective_url, captured_at, timestamp_precision,
  http_status, completeness, content_id?, document_id?,
  payload sizes/representation/encoding/object key,
  source provider/dataset/record ID, archive_record_key,
  links Array(Tuple(node_index, raw_href, target_url)), output_digest

material[_<build>].html_documents
  document_id FixedString(32) (sort key), content_id,
  representation, encoding, document_text,
  elements Array(Tuple(node/parent/subtree/sibling/depth/tag/namespace/
                       attributes/direct-text/text-offsets)), output_digest
```

Both are [MergeTree tables](https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree); sorting keys do not enforce uniqueness. URL and content projections on captures provide
alternate access paths. Application-level exact claims, deterministic output and
post-insert verification make retries safe; correctness does not depend on
background deduplication, `FINAL`, or an eventual upsert merge.

`public_v1.capture`, `html_element`, `link`, and `page` are the public SQL
contract. The query API binds these names to one selected build. Captures expose
complete parsed HTML; failed/non-HTML observations can exist internally without
pretending to be complete public HTML. The table DDL is
`packages/periplus/src/periplus/materialization/schema.sql`; views are in
`platform/clickhouse/public.sql` within that package.

There is no `ingest.*`, collection, fulfillment, acquisition-reason, frontier,
work queue or business table in ClickHouse.
