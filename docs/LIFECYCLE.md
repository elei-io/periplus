# Lifecycle

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
projection file, so an implementation-only edit cannot silently reuse the previous generation.

The current files project structural HTML and searchable body prose at content grain, plus link occurrences
and readiness membership at visit grain. HTML readiness also requires the active content root
marker because a separate batch may own shared-content output.

A visit batch loads visits and documents from a pinned snapshot, groups unique HTML
sources, and parses each body once. The batch containing the minimum retained HTML
`document_id` for each content hash owns its DOM output. The decision is deterministic
for that snapshot, including replay; every observation emits its own link occurrences.

Registry callbacks produce Arrow tables. Generic lifecycle code writes partitioned,
sorted immutable Parquet outside the commit claim. Under exact generation, observation
and content claims in Postgres, one lake transaction replaces the batch's visit-owned
and content-owned identities and registers its files. It then records completion in
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
physical-attempt authorization. Failures defer unstarted work and release physical allowance;
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
visible page. They distinguish domain pause, version-matching domain pacing, domain/global capacity,
global pacing, and physical allowance exhaustion. A policy edit invalidates an older stored domain
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
require recent crawler-process presence, available current admission/retained capacity, and unchanged
request controls. Browser dependency readiness is not an admission requirement. Description/SQL discovery, deadlines and later admission batches are unsupported;
they retain explicit unavailable reasons. Ranges include calculation time, five-second expiry,
sample count and uncertainty. Historical contention is not reconstructed; unchanged readiness and
competing work are assumptions, not promises or a statistical confidence interval.

### Current collection queue and progress

Current collection responses partition admitted waiting interests into `queue.runnable_pages`,
`deferred_pages`, and `unknown_pages`; these sum to `queued_pages`. Counts include retry waits,
exclude captures already dispatched and candidates not yet admitted, and are scoped to the request.
Runnable means eligible under the observed stored controls and fresh worker readiness. It reserves
no domain permit or browser slot. Domain/global pacing, capacity, pauses and attempt/time allowances
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
