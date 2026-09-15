# Ownership, capture evidence and background rebuilds

Design review, 2026-09-15, against `origin/main` at `5e7c572`. These are changes to
the experiment proposal, not to application or production storage. This review
supersedes the earlier five-table recommendation: start with **three `ingest.*`
tables**, keep durable customer requests in Postgres, and strengthen acquisition
evidence before implementing the ClickHouse writer.

## 1. Give each fact one authoritative owner

Choose by the decisions a record supports and its lifetime. A user ID does not
make an append-only analytical event transactional. Equally, a completed request
does not stop being a customer record when its crawler work finishes.

| Data | Authoritative home | Reason |
| --- | --- | --- |
| Accounts, organizations, membership, credentials, access rules | Postgres, when those features exist | Current identity and authorization |
| Request definitions, schedules, collections and frozen submitted specification | Postgres | What was requested, by whom, and under which terms |
| Collection lifecycle, budget reservations, final outcome/counters and seed-selection provenance | Postgres | Operational decisions and durable customer history |
| Pricing/entitlements and billable usage receipts | Postgres, when billing is implemented | Unique, transactional accounting; analytics cannot authorize charges |
| Frontier acquisitions, interests, deduplication and selection checkpoints | Postgres, bounded and pruned | Concurrent dispatch, cancellation and reservation decisions |
| Rate-limit settings, domain policy and crawler controls | Postgres | Editable policy |
| Work delivery, redelivery, worker presence and short domain permits | NATS JetStream/KV | Delivery and transient distributed coordination |
| Terminal visits with attempts, steps and retained-document metadata | ClickHouse `ingest.visits` | Immutable capture evidence and quality analysis |
| Collection-to-visit fulfillment and frozen dispatch causes | ClickHouse `ingest.fulfillments`, `ingest.acquisition_reasons` | Potentially corpus-sized result membership and provenance |
| DOM, text, links, structured data and their indexes | ClickHouse `material.*` | Rebuildable analytical output |
| Raw retained bytes | Existing immutable object repository | Source for reparsing and integrity verification |
| Query execution analytics | Private ClickHouse `observability.query_executions`, at query-service cutover | Latencies, failures, query patterns and resource measurements |
| Rebuild target, checkpoint and activation decision; retention decisions | Postgres | Small operational state; derived rows remain in ClickHouse |

No billing implementation or new generic event pipeline is implied by this table.
There is no need for Postgres CDC or a mirror of every control table to establish
this boundary.

### Collections should survive frontier cleanup

Today, [`cleanup_collections`](../../../packages/periplus/src/periplus/crawl/runtime/frontier_store.py)
waits for lake definition/outcome receipts, prunes dependent work, then deletes the
Postgres collection. [`history.py`](../../../packages/periplus/src/periplus/crawl/control/collections/history.py)
subsequently reconstructs customer history from lake tables. That is the boundary
I would change.

Keep the collection ID, frozen execution specification, creation/completion times,
terminal outcome, final counters and seed provenance in Postgres. Retain user or
organization ownership there when introduced. Separate customer-record retention
from raw-corpus retention. Expiring result bytes must not erase what the customer
ordered or make a completed order disappear.

Start by keeping the existing collection row, adding the terminal values currently
available only in `CollectionOutcome`, and clearing obsolete execution fields.
Keep immutable submitted intent distinct from editable priority/pause controls.
Request definitions and schedules can change without rewriting an earlier
collection's frozen specification. Do not invent separate order/run/version
frameworks unless the product actually needs those distinct identities.

Prune completed interests, navigation, acquisition payloads and delivered outbox
records after their required evidence is durable. Retain compact collections under
their own lifecycle. This removes `ingest.collections`,
`ingest.collection_outcomes`, their two message variants, their receipt gates,
and the live-versus-historical collection-detail implementation split.

The lake definition joins in [`lineage.py`](../../../packages/periplus/src/periplus/crawl/control/collections/lineage.py)
and [`arrivals.py`](../../../packages/periplus/src/periplus/crawl/control/collections/arrivals.py)
must change too. Resolve a bounded collection/permission request in Postgres, then
read result evidence by those IDs from ClickHouse. Retention must resolve retained
collection protection through the same owner, with bounded batches. Do not replace
the removed joins with an unbounded cross-database join or cached authorization.
Keep frozen policy/selection facts needed to interpret evidence after collection
expiry; a rule ID alone cannot reconstruct deleted rule text. Use the existing
frozen collection specification while retained and define the required lifetime
of private provenance before permitting its deletion.

### Result membership remains analytical

A collection can reference many visits; several collections can share one visit.
Those fulfillment rows grow with supplied results, not with the number of customer
orders. Keep the existing separate grains: dispatch cause, actual capture, and
later reuse. One `collection_id` column on `visits` cannot represent them.

Postgres owns whether a customer may access a collection and what is chargeable.
ClickHouse owns the retained list of resulting observations. Persist any future
charge decision under a unique operation identity in Postgres; do not bill from a
lagging ClickHouse count or a NATS delivery count. This is a distinction between
accounting and result facts, not two copies of the full result corpus.

Collection settlement, base-evidence availability and materialization readiness
remain separate. A request may be operationally finished while its results are
still being indexed. Names and UI states should make that explicit.

### Frontier size needs an enforced bound

Postgres is the natural home for the existing short transactional frontier work.
A few million rows is a workload to measure, not a reason to adopt an analytical
database for dispatch. But website politeness does **not** bound backlog: discovery
can enqueue faster than crawling, and multiple collections can create multiple
interests for one acquisition. Long-running collections retain deduplication state
even after individual acquisitions finish.

Bound admitted acquisitions, total retained interests and selection batch bytes,
and stop further discovery/admission when those limits are reached. The current
architecture explicitly lacks a retained-state quota. Preserve collection-local
deduplication and accounting while pruning finished execution payloads. Benchmark
dispatch and cleanup under contention, including the shared frontier-control row,
instead of assuming the corpus size determines this cost.

### Query logs: analytics and accounting have different contracts

Move the existing private query-history analytical workload to ClickHouse when the
query service moves. It can carry authenticated organization/user IDs and an
execution ID without copying user profiles. Keep saved queries, permissions and any
future durable usage ledger in Postgres.

[`QUERY_HISTORY.md`](../../QUERY_HISTORY.md) explicitly describes today's recorder
as best-effort and lossy; its `source` label is not authenticated user identity.
Neither becomes suitable for billing merely by changing databases. Preserve the
30-day private retention, bounded detail reads and raw SQL/parameter access rules
during the replacement. Use one recorder destination, update the dashboard queries,
then remove the superseded Postgres history table and cleanup path through Alembic.

ClickHouse's [`system.query_log`](https://clickhouse.com/docs/reference/system-tables/query_log)
provides engine execution details. Correlate it with application execution IDs;
it does not replace application events for admission rejection or work that never
reached the engine. Engine logs are per server and have their own flushing,
configuration and retention. Keep observability private and outside `public_v*`.

## 2. Lessons from Common Crawl

Common Crawl separates captured records (WARC), extracted metadata (WAT), and plain
text (WET). Its URL index includes retrieval status, content identity and type,
truncation, and archive location. That reinforces our separation of retained bytes,
capture evidence and replaceable materialization. It does not require adopting its
monthly publication cadence or copying its file layout.
[Capture formats](https://commoncrawl.org/get-started),
[URL index](https://commoncrawl.org/url-index)

The more useful lesson is evidence loss. Common Crawl documents historical missing
truncation indicators and loss of repeated HTTP headers in WAT. Once a fact was
discarded at acquisition, rebuilding indexes cannot restore it.
[Common Crawl errata](https://commoncrawl.org/errata)

### Changes before freezing the visit contract

Keep the single visit row and nested attempts. Add these facts at the attempt or
retained-representation boundary that actually observed them; retries can run under
different software versions and produce different responses.

| Addition or clarification | Concrete proposed contract | Why now |
| --- | --- | --- |
| Main-document HTTP observations | Bounded ordered `http_exchanges` inside each attempt: request URL/method, response URL/status/time when known, and duplicate-preserving response header pairs | Preserve HTTP redirects, original `Location`, full content type/charset and relevant response evidence |
| Observation coverage | Explicit exchange/header capture status such as `captured`, `unavailable` or `limited`, with reason | Empty arrays must not silently mean that nothing happened |
| Acquisition termination | Attempt-level `capture_end_reason` and terminal selection/skip reason, separate from success/failure | Useful HTML can be retained after a timeout or bounded completion |
| Retained representation completeness | A defined byte-capture state and optional truncation reason; retain existing per-step stopping reasons | A complete saved DOM snapshot is not a claim that the whole dynamic website finished loading |
| Capture provenance | Frozen producer release, capture method/serializer version and browser identity when observable, alongside existing policy | Explain changed capture behavior; keep unknown remote internals unknown |

Use `Array(Tuple(name String, value String))` for observed header entries, with
order as supplied by the acquisition API. Do not convert them to a dictionary or
claim original wire order/bytes. Playwright's
[`headers_array()`](https://playwright.dev/python/docs/api/class-response#response-headers-array)
preserves repeated entries; the project already pins a version containing it.
Record the disclosure/capture policy and bound total evidence bytes. Request
headers, credentials, subresources and complete browser network archives are not
an automatic expansion of this proposal.

Two actual losses in today's code make this more than speculative enrichment:

- [`capture.py`](../../../packages/periplus/src/periplus/crawl/acquisition/capture.py)
  can retain meaningful HTML after navigation times out and finish with a success
  attempt. The timeout flag is local and does not enter frozen success evidence.
- [`evidence.py`](../../../packages/periplus/src/periplus/crawl/acquisition/evidence.py)
  maps skipped attempts to successful physical attempts and drops their failure
  code; it also drops the intermediate attempt's response media type. Preserve the
  skip decision/reason independently of physical acquisition success.

Maintain the distinction between rendered HTML and response-body bytes. The
current HTML path stores UTF-8 serialization from the browser; it cannot recreate
the original network HTML or its original encoding. Retain original response
content-type information separately from stored representation charset. Do not
promise that parsing can undo rendering or recover discarded response bytes.

The [WARC standard](https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/)
also separates capture context, payload identity and truncation reasons. Our choice
is to preserve those distinctions in the existing evidence model, while keeping
one retained representation per visit initially. Supporting multiple representations
would be a deliberate new capture/storage contract, not a free rebuild feature.

### Things to retain or derive elsewhere

Keep the full content hash, stored/logical byte sizes, representation, observation
times, acquisition policy and independent failed/skipped visit rows. Identical
bytes can share parsing; visits with different URL, time, response headers or
collection attribution remain distinct observations. HTTP status and crawler
outcome must remain separate.

Titles, languages, canonical URLs, JSON-LD types and class vocabularies belong in
`material.*`: they can be recomputed from retained inputs. Host/reversed-host access
paths are useful candidates for native indexes or projections; do not treat a
particular normalization or public-suffix list as immutable source evidence.

Common Crawl's file/offset/length locator is also a useful storage experiment.
Benchmark the cost of very many small raw objects against packed, independently
readable records before billion-content sizing. Packing has lookup, compaction,
deletion and recovery costs; adopting ClickHouse does not solve those for the
separate raw repository. Keep content identity independent of any future physical
locator. The current object key remains the implemented location contract; no
unneeded packer or locator service is introduced now.

The accompanying DDL now has three tables but still models existing frozen visit
fields. The additions above require acquisition/validator work and are **not yet
implemented in that DDL or in the crawler**. The schema is not ready to freeze
until they have concrete bounds and acquisition tests.

## 3. Background rebuilds remain a first-class operation

Build replacement output for the affected semantic family while the current
output continues serving queries. Reuse retained raw bytes and the same bounded
materializer function. Rebuild content-owned rows once per distinct content and
version; separately rebuild visit-owned URL resolution and attribution as needed.
Deduplicate bounded input pages rather than loading all identities into Python.

The procedure should be:

1. Create the replacement target and its durable live-event consumer **before**
   scanning history. Use `DeliverAll` over retained visit events, not `DeliverNew`.
   Two role consumers normally suffice; allow one additional consumer for one
   active replacement run, not one per output table. Provision through the explicit
   rebuild lifecycle, never a request polling path.
2. Protect the selected source evidence/raw objects from retirement for the run.
   Scan authoritative ClickHouse inputs in finite partition/key ranges with bounded
   checkpoints and outstanding work. A key maximum bounds enumeration, not commits.
3. Process retained/new events into the replacement concurrently, with identical
   identity/replay rules. An event may have been ACKed by the old materializer
   before its base row committed; this is why reading only new events is insufficient.
4. Reconcile duplicates and failures, establish complete output and authoritative
   base visibility, and verify historical coverage plus catch-up through a recorded
   stream boundary. Consumer ACKs alone are not proof of database completeness.
5. Activate the complete replacement. Preserve its live consumer as the active
   materializer consumer, avoiding a delivery gap from deleting/recreating it.
   Retire the old consumer and output only after in-flight work is drained and the
   validation/rollback period ends. Keep this small target selection in Postgres;
   versions and consumer names remain private implementation details.

The coverage argument is conditional on durable ingestion: an older event removed
after all required ACKs must already have accepted base evidence visible to the
scan; an older pending event remains available to the replacement consumer; a later
event reaches that consumer directly. Parked failures, retention, replica lag and
restore boundaries require explicit reconciliation. NATS is pending-work storage,
not a permanent historical archive.
[Retention](https://docs.nats.io/learn/jetstream/retention-policies),
[consumer delivery policies](https://github.com/nats-io/nats.docs/blob/master/nats-concepts/jetstream/consumers.md#deliverpolicy)

**Bounded local check:** an isolated NATS 2.11.17 server with file-backed Interest
retention and nats-py 2.15.0 delivered events `[2, 3, 4, 5]` to a newly created
replacement consumer. Event 1 had already been ACKed by both original consumers
and removed; event 2 was pending only on ingestion, event 3 only on materialization,
event 4 on both, and event 5 arrived after consumer creation. Original-consumer
ACKs did not remove replacement-pending events. ACKing the replacement released
all remaining messages. The simulated historical set `{1, 3}` plus replay covered
all five identities. The disposable server was removed. This verifies delivery
overlap, not ClickHouse publication, replication, crash recovery or throughput.

Give live work priority and cap backfill CPU, memory, raw reads and inserts. Budget
disk for old output plus replacement output plus merge headroom, with raw storage
shared. A rebuild that cannot keep up with continuing arrivals needs more capacity
or less admitted work; it cannot be made current by changing the activation flag.
Both active and replacement consumers need live processing capacity: a stalled
replacement retains messages in the shared Interest stream and can eventually
block new publication. Throttle historical scanning first; cancellation must
explicitly retire the replacement consumer and release its protections.

For one table pair in an Atomic database,
[`EXCHANGE TABLES`](https://clickhouse.com/docs/reference/statements/exchange)
provides an atomic name exchange. Multiple pairs are exchanged sequentially; this
is not a whole-catalogue transaction. Keep targets explicit and validate writer/view
bindings. For a dependent family, use a controlled query-service activation or a
brief maintenance window. Unchanged public semantics keep their existing `public_v*`
contract; an internal parser version does not itself require an API version bump.

Only the affected family needs rebuilding. An index change need not reparse HTML,
and a text-extraction change need not rebuild unrelated URL metadata. The remaining
publication and failure tests in [the pipeline plan](PIPELINE_PLAN.md) are still
required before calling background rebuilds production-ready.

## 4. Make the janitor's deletions explainable

The current [`janitor.py`](../../../packages/periplus/src/periplus/operations/janitor.py)
does more than transient cleanup: it prunes frontier work, transfers completed
collections to lake-backed history, expires private SQL logs, retires logical
evidence, reconciles abandoned raw publications, and deletes raw objects after
snapshot checks. Physical registered-file compaction already belongs to
LakeDucktor, so removing that is not a deletion from the janitor itself.

Several hidden dependencies explain why its behavior is hard to follow:

- [`current_roots`](../../../packages/periplus/src/periplus/retention/runtime.py)
  treats the mere existence of a Postgres collection as a retention root. Keeping
  customer collections forever without replacing this rule would protect their
  results forever too.
- [`RetentionCatalogue.plan`](../../../packages/periplus/src/periplus/retention/catalogue.py)
  discovers eligibility through visit/document/fulfillment/collection/outcome
  joins. Returning 25 candidates does not prove that the query scanned little data.
- Projection deletion discovers active, rebuilding and retired targets by table
  name prefixes. Raw deletion depends on snapshot anchoring and an oldest-snapshot
  threshold in [`retention/store.py`](../../../packages/periplus/src/periplus/retention/store.py).
- In the current process loop, navigation and probe cleanup share one reported
  phase; several destructive stages share `lake_retention`. A failure or successful
  sweep does not by itself explain which action was blocked or completed.

Keep **one janitor process with three explicit jobs**, using domain-specific
functions rather than a generic maintenance workflow framework:

| Job | Work to retain | What becomes removable |
| --- | --- | --- |
| Prune completed operational work | Bounded Postgres transactions for finished interests, acquisitions and delivered outbox payloads; navigation and abandoned probe cleanup | Collection transfer/deletion, its two lake receipts and live/history switching |
| Execute explicit result-retention decisions | Resolve policy and active protections; retire eligible result identities; request bounded ClickHouse deletion; clean explicitly retired material targets | Normalized visit-child deletion, full registry/name-prefix discovery, DuckLake transactions and snapshot bookkeeping |
| Reclaim unreferenced raw objects | Reconcile abandoned uploads, check exact references and active work, honor reader/restore protection, delete and record completion | Lake snapshot anchoring/oldest-snapshot joins once the replacement protection contract is proven |

The collection owner decides customer-record retention. The rebuild owner marks
an exact output target retired and supplies its cleanup boundary; janitor executes
that decision. Ingestors/materializers own retries and repair. The janitor must not
declare work successful, silently abandon a dead letter, or initiate reparsing to
make a cleanup condition pass.

ClickHouse owns its parts, merges, native index storage and obsolete-part cleanup.
Use native TTL for private query logs and similarly independent time-retained data;
delete the Postgres query-history cleanup phase with that cutover. TTL is applied
asynchronously during merges, so query-history reads still enforce the retention
cutoff. Scheduled `OPTIMIZE FINAL` is not a janitor replacement.
[ClickHouse TTL](https://clickhouse.com/docs/concepts/features/operations/delete/ttl)

Do not use a blanket visit-age TTL or raw-bucket lifecycle to implement shared
content retention. A later collection can still need old bytes. An old date, a
missing KV key or an empty lagging-replica result cannot authorize deletion.

### An explicit raw-deletion sequence

1. **Decide:** select a bounded candidate set from explicit expired protection or
   retirement work. Resolve all remaining collection protections and active capture,
   outbox, ingestion, materialization, rebuild and parked-failure references. Use
   native visit/content/collection access paths and paginated proof of absence;
   a truncated reference list never proves there are no remaining owners.
2. **Fence and retire:** under exact identity ownership, recheck eligibility and
   record the decision. Stop new references/replays from crossing that decision.
   Remove the logical rows from query visibility and complete required dependent
   deletions on the authoritative route. Persist enough state to resume a crash.
3. **Reclaim:** once retained references and active readers are gone, and the
   documented reader/backup-restore protection has elapsed or been satisfied,
   delete the raw object and record completion. Shared content is reclaimed only
   after its final protected reference is gone.

ClickHouse mutations can be asynchronous and rewrite substantial data. Batch
retirements by table/partition, track completion and preserve query consistency
across dependent tables. A submitted deletion is not a completed deletion, and
deleting base rows does not automatically cascade through materialized-view
targets or the raw store.
[Deletion mutations](https://clickhouse.com/docs/reference/statements/alter/delete)

Keep Postgres records for pending deletion work and exact coordination. The current
permanently retained per-identity tombstones are a separate growth concern: they
can scale with all retired evidence. Their placement/compaction and bounded replay
contract need an explicit decision before large-scale purge. Do not expire them
as routine operational clutter or claim Postgres stays bounded while ignoring them.

This deletion protocol remains a correctness gate for the experiment. Keep
destructive corpus retention disabled until retirement-versus-replay, rebuild,
replica lag, shared-content and crash-recovery cases pass. Existing authorized
operational cleanup can continue independently.

### Show what happened, not just that a sweep ran

Each bounded phase should report candidates checked, retained, deletion requested,
deletion completed, bytes actually removed, duration and checkpoint. Use concrete
defer reasons such as `active_frontier`, `retained_collection`, `pending_ingestion`,
`active_rebuild`, `unresolved_failure`, `reader_grace` and `catalogue_unavailable`.
Keep identities in bounded diagnostic records, not metric labels. A dry run should
show the same eligibility rules, followed by revalidation under ownership when a
real deletion is attempted.

The cleanup acceptance case is inspectable: retain a completed customer collection,
prune its finished frontier payloads, expire one of two collections sharing content
and verify the bytes remain, then remove the final protection and verify resumable
deletion after the reader/rebuild barrier. Interrupt each stage. Also verify a
queued materializer and an unresolved dead letter continue to protect raw bytes.
