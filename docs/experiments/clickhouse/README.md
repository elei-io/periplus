# ClickHouse experiment

The [implementation exit criteria](EXIT_CRITERIA.md) define the first vertical
slice, recovery/rebuild/retention acceptance, performance targets and cleanup needed
to call this replacement complete.

The [pipeline replacement and cleanup plan](PIPELINE_PLAN.md) maps the proposed
JetStream fan-out, smaller workers, native ClickHouse responsibilities, remaining
correctness gates and concrete code deletions.

The [design review](DESIGN_REVIEW.md) tightens operational/analytical ownership,
checks background rebuild delivery and compares capture evidence with Common
Crawl. It replaces the earlier five-table recommendation with three tables.

Start with **three ingestion tables instead of eight**. Put a terminal visit,
its attempts, their steps, and its optional document reference into one immutable
row. Keep fulfillment and dispatch-cause evidence as independently appended
relations. Keep collection definitions, lifecycle and final request accounting
in Postgres. The proposed [DDL](ingest.sql) is executable against a scratch database;
it is not installed by Compose or used by Periplus yet. It still uses the existing
visit fields: the review identifies additional acquisition evidence required
before the new contract is frozen.

This experiment starts from `origin/main` at `5e7c572`. The first implementation
adds only a local ClickHouse server. Application ingestion, materialization, public
SQL and production deployment continue to use the existing implementation.
See [local setup](../../DEPLOYMENT.md#local-clickhouse-experiment).

## Proposed `ingest.*`

| Relation | Grain and contents | Change from today |
| --- | --- | --- |
| `ingest.visits` | One terminal acquisition: URL, times, outcome, HTTP status, policy, optional retained-content reference, ordered attempts with ordered steps | Combines `visits`, `documents`, `attempts`, and `steps` |
| `ingest.fulfillments` | One collection URL supplied by an observation, including reuse and traversal context | Retain independently appended evidence |
| `ingest.acquisition_reasons` | One dispatch cause linking an acquisition to a collection | Retain independently appended evidence |

This follows the actual [VisitEvidence boundary](../../../packages/periplus/src/periplus/platform/catalogue/records.py):
the crawler already freezes the complete visit package before ingestion. Its
children never need an independent later update. A normal capture therefore
becomes one row in one table, eliminating the four-table capture publication
problem. It also removes the visit-to-document join from the ordinary capture
query and materialization input lookup.

Collections remain durable customer records in Postgres after frontier work is
pruned. A later collection can reuse an existing visit and append a fulfillment.
Embedding that evidence in a visit would require rewriting old acquisitions.
Dispatch reasons and fulfillments remain distinct facts: causing a capture and
later using its result have different meanings. These relations can grow with
the result corpus and belong in ClickHouse. Their collection IDs link to control
records; they do not authorize access or represent a billing ledger.

## What the visit row contains

Frequently filtered fields are typed columns. Optional document metadata is flat
and nullable: absence is different from an empty string or zero-byte content.
Attempts use `Array(Tuple(..., steps Array(Tuple(...))))`; explicit indexes and
attempt IDs preserve order and identity. Their parent visit/attempt references
are implicit in containment. `ARRAY JOIN` exposes these records when needed.
ClickHouse supports [arrays of tuples and nested arrays](https://clickhouse.com/docs/reference/data-types/array).

For example, terminal attempts can be inspected without a join to another table:

```sql
SELECT visit_id, a.attempt_index, a.outcome, a.finished_at,
       arrayMap(s -> s.action, a.steps) AS completion_actions
FROM ingest.visits
ARRAY JOIN attempts AS a
WHERE requested_url = 'https://example.com/'
ORDER BY finished_at, visit_id, a.attempt_index;
```

The zero-attempt case retains an empty array. An uncertain attempt retains a null
finish time. Failed visits remain rows even without content. Preserve both visit
and document observation timestamps; the current validation does not establish
their equality, so this proposal does not silently discard one.

`content_sha256` is the complete 32-byte binary SHA-256, with `unhex()` on input
and `lower(hex())` at a public boundary. It remains nullable when there is no
document. Raw bytes stay immutable in the existing object repository; only their
repository-relative key, sizes, encoding, representation and identity enter the
row. Repeated captures repeat small reference metadata while sharing raw bytes
and future content-owned materializations. No separate authoritative content
dictionary or allocation service is required for this first experiment.

Frozen policy, resource usage and step parameters use canonical JSON **strings**
in this proposal. They preserve the
existing JSON value without introducing dynamic column discovery into the
ingestion contract. Typed fields or derived JSON columns can be added for a
measured query. This does not put HTML, DOM trees, or search postings into one
giant visit payload: those belong to raw objects and `material.*`.

The writer must enforce the existing frozen-evidence validation, including the
complete optional-document group, contiguous attempt/step indexes, valid child
references, policy presence, times, and readable content-correct objects. It must
bound bytes and child counts before inserting. The DDL alone does not enforce
those invariants; silently truncating a large envelope is not acceptable.

## First physical layout

Use plain `MergeTree` for immutable evidence. Visits are partitioned by terminal
month and sorted by `(requested_url, finished_at, visit_id)`. This favors URL
history and groups repeated per-site metadata for compression. A lightweight
`by_visit` projection indexes visit identity with `_part_offset`. Fulfillments
are sorted by collection and URL, with a similar observation projection.
These are [engine-maintained alternate access paths](https://clickhouse.com/docs/concepts/features/projections/projections),
not application-maintained posting tables.

The two lineage relations keep their existing grains but get sort orders that
match their main identity lookups. Monthly partitions are a starting maintenance
boundary, not a query index; an all-history ID lookup still consults retained
parts. Compare unpartitioned and monthly layouts, and add a native content-hash
access path when exercising content download/reverse lookup. The initial DDL
does not promise that content lookup is selective yet.

Use `LowCardinality` for repeated status, representation, media, and mode strings;
keep URLs as ordinary strings. Compare compression and complete reads on real
captures before introducing global dictionaries. Neither sorting nor the
[MergeTree primary index](https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/mergetree)
enforces uniqueness. No `ReplacingMergeTree`, `FINAL`, update, or deduplication
setting in this proposal silently weakens the immutable-evidence contract.

## Ingestion and publication to prove next

Keep the existing JetStream lane and a bounded Periplus ingestor initially.
It validates frozen jobs, groups visit rows into byte/time-bounded inserts, and
reconciles retries under exact identity ownership. Identical existing evidence
is a no-op; conflicting identity reuse fails. Native block deduplication alone
cannot establish permanent row uniqueness across regrouped or delayed retries.
The next writer experiment must prove uncertain insert recovery and concurrent
duplicate delivery before this becomes an application storage contract.

A complete visit is one row and needs no cross-table capture transaction. This
does **not** make a mixed batch, multiple partitions, downstream materialization,
or a Postgres receipt one atomic transaction. ClickHouse documents its
[insert transaction boundaries](https://clickhouse.com/docs/concepts/features/operations/insert/transactions)
explicitly. Success/ACK ordering, durable write settings and recovery need their
own failure tests.

The pipeline plan proposes replacing DuckLake CDC with independent ingestor and
materializer consumers of the same frozen capture event. This removes the need
for a second publication intent after ingestion commits. Keep the existing
frontier producer outbox and bounded evidence receipts. Reconcile uncertain writes
before retrying, and establish routing/read-after-write visibility before using
replicated servers: a lagging replica cannot prove absence. See the
[transport and recovery details](PIPELINE_PLAN.md#2-use-jetstream-persistence-directly).

`material.*` publication and public snapshot semantics remain a separate design
decision. There are no parser/build/generation columns in the proposed public
contract; public versioning remains `public_v*`. New visits should become
materialization work within minutes, independently of compaction or a full rebuild.

## Run the proposal manually

The Compose server creates an empty `ingest` database. To install these candidate
tables there for exploration, run from the repository root:

```sh
docker compose exec -T periplus-clickhouse sh -c \
  'exec clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database ingest --multiquery' \
  < docs/experiments/clickhouse/ingest.sql
```

The DDL intentionally fails when a table already exists. It is not an idempotent
schema reconciler or migration system. When the application adopts an approved
contract, wire its installation into the setup lifecycle and use the repository's
Alembic workflow for control-state changes. Do not add startup schema repair to
ordinary workers.

The next useful acceptance case is the existing frozen ingestion job written
through JetStream, recovered after an interrupted commit, and queried for its
complete capture and ordered attempts/steps. Then measure batching, append
freshness, parts, merges, compression, and lookup work using the shared
`benchmarks/query/` bench. A working local server and DDL are not performance or
durability evidence for the homelab.

## Local setup verification

Verified on the pinned 26.8.2.7 arm64 image:

- Compose configuration validates and the authenticated SQL health check passes.
- Host HTTP SQL succeeds with credentials; anonymous SQL is rejected.
- The current three-table proposal and both lightweight projections create
  successfully in a scratch database.
- Synthetic records preserve ordered nested steps, Unicode JSON strings,
  microsecond timestamps, binary SHA-256, absent content, empty attempts and an
  uncertain attempt's null finish time.
- Before the collection-table removal, complete ordered row exports were identical
  before and after forced container recreation. The current three-table DDL and
  the unchanged visit/lineage row checks were rerun after that removal.

The scratch database was removed; `ingest` remains empty. These checks do not
exercise application validation, replay, power loss, replication or performance.
No Python application or frontend code changed, so validation was confined to
Compose, the running server and the proposed SQL.
