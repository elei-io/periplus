# Lifecycle

The canonical element replacement is specified in [ELEMENT_LAYOUT.md](ELEMENT_LAYOUT.md).

Periplus keeps acquisition small and derived evidence append-only while retained.
Explicit retirement is the sole deletion path; see [RETENTION.md](RETENTION.md).

## Ingestion

Acquisition stores immutable document bytes before publishing a frozen ingestion job. Ingestion
then inserts the terminal crawl/visit evidence, ordered attempts and steps, and optional document
reference. It never waits for materialization. External HTML enters at the same immutable-byte
boundary.

Immutable `ingest.*` evidence is the complete rebuild authority. Identity replay with the same
evidence is a no-op; conflicting evidence fails. Materialization consumes inserted visits only.

Every ingestor replica is symmetric; there is no ingestion coordinator or elected owner. At
process startup a replica opens one NATS session, validates the shared stream, durable consumer,
result store, and operation-lease contracts once, then creates one pull handle per local lane.
`PERIPLUS_INGESTOR_CONCURRENCY` selects one to four lanes per replica. Each lane owns one independent
DuckLake connection, while all lanes reuse the process-owned queue session and handles.

Replicas compete on the same durable consumer. Its fixed global unacknowledged-delivery ceiling is
independent of local concurrency, so horizontal replicas increase active writers until that
cluster safety bound is reached. A lane holds renewable request-scoped leases while committing a
batch and heartbeats its deliveries. Bounded local retries replay the same frozen evidence after
DuckLake transaction conflicts. ACK happens only after the append and durable result state
succeed; a process failure therefore causes another replica to receive and idempotently reconcile
the work.

## Registry-driven projection

The fixed registry is discovered from `materialization/projections/*.py`. Each non-private file is
one complete materialization: physical and Arrow columns, ownership grain, identity, validation,
partition transforms, sorting, projector, and description. There is no central list or second
physical-schema declaration. Add one file, edit one file, or delete one file; then redeploy and run
a complete rebuild. The generation digest includes the complete source of every discovered
projection file and its declared implementation dependencies, so an implementation-only edit cannot silently reuse the previous generation.

The current files project complete HTML elements and JSON-LD at content grain,
with links and readiness at visit grain. HTML readiness requires the null-parent
element because a separate batch may own shared-content output.

A visit batch loads visits and documents from a pinned snapshot, groups unique HTML
sources, and parses each body once. The batch containing the minimum retained HTML
`document_id` for each content hash owns its DOM output. The decision is deterministic
for that snapshot, including replay; every observation emits its own link occurrences.

Registry callbacks produce Arrow tables. Every element stores complete descendant
text and direct text. Preparation needs no vocabulary allocation, tokenizer or
lake transaction. Large nested pages can amplify temporary preparation memory;
worker concurrency and document limits bound admission.

Generic lifecycle code writes partitioned,
sorted immutable Parquet outside the commit claim. Under exact generation, observation
and content claims in Postgres, one lake transaction replaces the batch's visit-owned
and content-owned identities and registers its files. Clean, fixed-snapshot initial
batches with original minimum-document ownership can omit replacement on their first
publication. Under the existing claim, a short control transaction durably records
`materialization_batches.write_intent_at` before lake I/O. An existing intent without
a completion receipt always uses replacement. Initial plans reject overlapping visit
IDs; catch-up, live batches and retirement-reassigned owners retain replacement.
Intent transaction failures abort before lake I/O, including uncertain outcomes. It then records completion in
Postgres `materialization_applied_batches`, and only then ACKs delivery. A missing receipt
after a successful lake commit causes the same identity replacement, so replay cannot
append duplicates. A durable receipt makes redelivery a no-op. Preparation remains
parallel; commits within one generation are serialized. See [RETENTION.md](RETENTION.md)
for bounded claim ownership and uncertain-commit handling.

Partition transforms are declared per registry entry; there is no global material partition
policy. Content-owned HTML currently use eight content-hash buckets because exact
content lookup is their dominant access path. Visit-owned link occurrences use
`month(observed_at)` to keep chronological appends coherent without multiplying every batch into
many small URL-bucket files; they are sorted by source URL, target URL, time, and occurrence
identity. A projection may instead declare day, year, bucket, multiple transforms, or no
partitioning. Rebuild visits are ordered chronologically so monthly files remain coherent. Rebuilds
default to 500 visits per batch. LakeDucktor alone compacts and reclaims unreferenced files; Periplus
never deletes registered material data.

The process-owned DuckLake connection factory selects one storage protocol for attachment,
material file writes, size inspection, and registration. Filesystem protocols register names
relative to the shared working directory. URI protocols register the complete immutable object
URI. Materialization code does not branch by storage backend.

## Complete rebuild

1. Periplus Postgres records the source snapshot, registry digest, and visit batch identities.
2. The planner creates every discovered hidden relation from the registry.
3. Workers prepare files in parallel, commit deterministic identity replacements, and
   record applied receipts in control Postgres.
4. Activation checks the exact registry, validates every hidden relation, and catches up visits
   inserted after the pinned snapshot.
5. Under old/new generation claims, one DuckLake transaction swaps every relation and
   public view. Postgres `materialization_state` then records the published generation.
   Retired table names identify a completed swap if its Postgres acknowledgement was lost.
   Readiness is unknown while activation is in progress.
6. Retired tables remain until completion is durably recorded and post-activation checks pass.

A completed activation delivery from a different deployed registry or a superseded
generation is acknowledged without deleting its retired tables. It cannot be validated
against the new registry, and retrying it would block the single activation lane.
After the matching current generation passes all post-activation checks, finalization
also removes retired markers for earlier durably completed swaps. Physical marker names
select the exact old relations, including projections removed from the registry;
pending swaps and hidden rebuild tables are excluded. Until a matching replacement
passes validation, deferred retired tables stay registered. A cleanup failure retries
the current delivery and never bypasses validation.

Post-activation checks pin physical reads with `AT (VERSION => snapshot)` and let
each query finish its own transaction. They must not hold one metadata transaction
across the complete validation, which can exceed the fixed remote transaction
timeout. Array-sorting and expanding reference checks first
copy only their required columns into 64 temporary content-hash partitions
with Snappy compression to reduce staging CPU. Staging flushes the partition writer
thread buffer and Parquet row groups at 2,048 rows, rather than retaining large
nested-array buffers across all partitions. The prior connection flush setting is
restored on success and failure. Checks then
run the original predicates on matching partitions. Equal content identities stay
together, so missing references and duplicates remain detectable. This avoids a
global occurrence expansion and repeated lake scans. Temporary files are removed
on normal completion and exceptions; they are not registered lake data. Staging
and each validation query report progress. These partitions reduce intermediate
size; a single oversized content or skewed partition can still exceed a worker's
fixed memory or temporary-storage budget and must fail without skipping checks.

A registry/schema change cannot be applied to a running or active generation with a different
digest. Partial activation and per-table repair do not exist.

## Live CDC and recovery

One dedicated insert-only DuckLake CDC consumer follows `ingest.visits`. Every materializer
replica is a symmetric coordinator candidate. A renewable NATS operation lease suppresses
cross-replica connection contention; its current holder then acquires the DuckLake consumer's
owner-token lease. Neither lease stores a cursor, and no replica is statically designated. If the
holder exits or loses either lease, another replica takes over after expiry and continues the same
durable DuckLake consumer. Concurrent consumer creation remains idempotent.

The elected connection turns each snapshot window into deterministic visit batches on the same
JetStream lane as rebuild work. The CDC cursor advances only after every applied marker is durable.
Restarting or failing over before cursor commit replays the same batch identities.

An unreadable registered file invalidates the complete generation. Periplus ensures a replacement
rebuild exists, fences the active generation, and rebuilds every discovered relation from unchanged
ingestion evidence and immutable objects. It never repairs one table or one file in place.

## Query

The complete public query contract and its grains are defined in [`SCHEMA.md`](SCHEMA.md). A
measured recurring query may justify a new fixed expensive projection, but it must enter as one
projection file and use the same append-only lifecycle.

Views and macros do not belong to projection files. A separate lightweight public registry owns
the `public_v1.*` SQL resources and declares their required material relations. This
keeps the runtime API independently evolvable while making installation fail if any dependency of
the public contract is missing.

## Acquisition dependency checks

Each crawler checks ingestion delivery, repository storage, and its standard CDP endpoint before
calling transactional dispatch. A successful check is usable for five seconds; known failure backs
that replica's dispatcher off for thirty seconds. The check does not open DuckLake or wait for an
ingestor/materializer. It runs outside PostgreSQL transactions, so it is evidence of recent health,
not a guarantee that a dependency will remain available after authorization.

Storage readiness performs a conditional write, bounded read-back, and deletion of a small unique
`runtime/probes/` object using the configured repository. Each acquisition pipeline owns at most one
in-flight storage probe, retained after a caller's five-second timeout or cancellation; concurrent
callers share it. Successful storage results are cached for five seconds from probe completion.
Shutdown drains that operation. The janitor scans bounded metadata batches and reclaims probe objects
older than two hours after a process crash. Immutable HTML/document evidence is never a probe target.

Capture deliveries recheck delivery and storage health and establish the browser connection before
physical-attempt authorization. Failures defer unstarted work and release the dispatch slot;
`ingestion_delivery_unavailable`, `storage_unavailable`, and `cdp_unavailable` explain that wait.
The original request page charge, if dispatch already committed, remains consumed and is not repeated.

Crawler heartbeats publish the dispatch check state in the existing ephemeral worker KV bucket.
A ready report expires after five seconds even if its dispatch loop stalls; blocked reports name
only delivery, storage, or CDP, never raw dependency errors. `/frontier/live` reads a startup-owned
handle with one concurrent read, a two-second timeout/cache, and a 128-report preview limit. It
excludes server-timestamped heartbeats older than fifteen seconds, malformed reports, and implausible
future reports. Counts describe observed reports, not guaranteed available capacity. Missing or
unreadable presence leaves availability unknown. Public responses omit worker IDs; work from every request class is visible.

## Current frontier wait explanations

Item reads resolve current domain policy with the same specificity as dispatch, for only the bounded
visible page. They distinguish domain pause, version-matching domain pacing, domain capacity and global dispatch capacity. A policy edit invalidates an older stored domain
pacing hint. The eligibility floor combines applicable persisted timing constraints; it is not a
promised dispatch time. Reads return constraint names without exposing unrelated caller details,
or occupancy counts. Live domain permits and future worker capacity can still prevent a start after
that floor, so these explanations do not by themselves establish an estimate range.


### Frozen candidates awaiting admission

Current collection responses include an `admission` snapshot. `pending_candidates` counts remaining
entries in frozen seed and follow selections; it does not count unresolved discovery or promise
unique newly acquired pages. `preview_urls` contains at most five entries from that collection,
without claiming dispatch order. `oldest_selected_at` and `elapsed_seconds` describe selection age
at the enclosing response's `as_of`; an absent timestamp remains unknown. A conditional first-admission range can be supplied for direct single-URL requests using recent
comparable observations. Unsupported work or insufficient evidence has an explicit unavailable reason. Collection pause,
settlement, and known admission constraints remain distinct from selection still being unresolved.


Due collection resolution/admission service honors bounded request priority: service age is shifted
by priority seconds (-10 through 10). Completing a service pass refreshes its due timestamp, allowing
older requests to overtake repeatedly served higher-priority work. Not-yet-due dependency backoff
and active service leases are excluded before ranking. This is separate from dispatch's scheduling
turns and never grants a public global queue position.


First-admission estimates measure submission to the first committed interest, including the initial
selection service wait. They use three to twenty recent public single-URL requests on the same
hostname, under the same crawler control version, priority and recent-result age setting. Samples
are drawn from the last ten minutes and must include an admission within two minutes. Predictions
require recent crawler-process presence and unchanged
request controls. Browser dependency readiness is not an admission requirement. Description/SQL discovery, deadlines and later admission batches are unsupported;
they retain explicit unavailable reasons. Ranges include calculation time, five-second expiry,
sample count and uncertainty. Historical contention is not reconstructed; unchanged readiness and
competing work are assumptions, not promises or a statistical confidence interval.

### Current collection queue and progress

Current collection responses partition admitted waiting interests into `queue.runnable_pages`,
`deferred_pages`, and `unknown_pages`; these sum to `queued_pages`. Counts include retry waits,
exclude captures already dispatched and candidates not yet admitted, and are scoped to the request.
Runnable means eligible under the observed stored controls and fresh worker readiness. It reserves
no domain permit or browser slot. Domain pacing, concurrency, pauses and per-acquisition retry limits
produce explicit constraints. Missing readiness/policy evidence and exclusions requiring candidate
inspection produce unknown eligibility rather than an optimistic runnable count.

`oldest_admitted_at` and `oldest_wait_seconds` describe the oldest currently queued interest at
`as_of`, including its elapsed time across retries. No waiting interests yields null age.
`last_progress_at` records execution progress: admission, frozen or advanced selection, dispatch,
physical authorization, retry outcome, fulfillment and settlement. Polling, claim renewal,
waiting-reason updates and priority/pause edits do not advance it. Catalogue commit/readiness
observations remain separate fields. These operational summaries retire with current collection state.

## Scheduled request execution

[SCHEDULES.md](SCHEDULES.md) defines reusable intent, cron/interval evaluation,
transactional creation, overlap suppression, maximum count and missed-tick behavior.
The crawler no longer runs a separate background selection loop.

Request intent stores optional `max_duration_seconds` (1–31,536,000). Creation freezes
`deadline_at = created_at + duration` in execution intent. Idempotent submission never
renews it. Waiting and paused time count. Expiry prevents new selection, admission and
capture starts, waits for already-started captures, and settles as `duration_limit`.
Ingestion and materialization proceed independently afterward. Schedule stop bounds
creation only; each generated request receives a fresh duration budget.

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.


## Rebuild timing visibility

The Grafana dashboard's **Materialization time by operation (all attempts)** panel
uses `periplus_materialization_step_duration_seconds`. Its bounded `step` and
`outcome` labels distinguish source lookup, HTML read/decode, HTML parsing,
projection row construction,
Parquet encoding/upload, file-size lookup,
commit-claim acquisition, the lake transaction, and the Postgres receipt write.
Every completed operation attempt is observed, including exceptions and retries.
An in-progress operation or a process killed before observation is not included.
Batch output row/byte totals describe registered projection files.
These metrics do not persist in batch receipts; Prometheus retention controls history.

The panel sums worker wall seconds per second across replicas, not CPU seconds
or total rebuild duration. Steps have different observation counts (per document,
projection, file, or batch), so compare summed durations rather than averages.
Do not add these detailed durations to the existing coarse phase durations.
Claim acquisition includes database access and contention/retry waits; the lake
transaction includes BEGIN, statements, and COMMIT/rollback but excludes claim
acquisition. HTML reads include storage decompression and decoding.

DuckDB currently performs Parquet encoding and remote upload in one COPY call.
`parquet_encode_upload` intentionally reports the combined duration: it cannot
attribute encoding versus network waits. Separating those requires engine-level
instrumentation or a separately evaluated staging/upload change; no estimate is
presented as a measured split. Claim release and small Python bookkeeping gaps
are not measured by the detailed steps. Deploy the worker and updated dashboard
to collect and display these metrics; no projection rebuild is required solely
for this instrumentation.

### Ingestion delivery recovery

Ingestion receipts retain the JetStream publication sequence beside the immutable
job. Receipt recovery checks that exact stored delivery and its payload before
republishing. A queued or in-flight message remains the consumer's responsibility;
a missing delivery or expired receipt replays the original job, never a new crawl.
An unavailable stream lookup defers recovery rather than creating another copy.
Receipts without a publication sequence establish one on their next pending replay.
The existing queue is drained normally; no duplicate purge is required.

An ingestion batch reserves each job's operation identities without waiting. Jobs
whose identities are held by another worker are deferred individually; unrelated
jobs commit together, sharing identities already owned within that batch. Duplicate
request IDs in a batch are deferred until their first delivery has a durable receipt.
Receipt writes precede ACKs. Cancellation, lease expiry, missing receipts and failed
acknowledgements leave replayable work, with immutable lake identity checks and
Postgres write claims retaining their existing authority.

Ingestion also attempts PostgreSQL write claims without waiting. A rejected claim
reports its exact blocked identities and expiry; only jobs touching those identities
are delayed, and the remaining batch retries its idempotent writes. A visit write
that committed before a lineage rejection remains safe to replay. Deferred jobs
receive no success receipt or acknowledgement and consume no processing-failure
budget. Redelivery uses the remaining claim lifetime capped at 30 seconds plus
jitter, allowing early releases to become useful without occupying a writer lane.
Uncertain writes retain their original claims and fail-stop bounds.

Elements share one parsed document context. Exact descendant text is materialized
for every element; parser nodes are temporary. A complete rebuild activates all
new projections together. Normal finalization removes obsolete material tables.
