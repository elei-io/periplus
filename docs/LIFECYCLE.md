# Lifecycle

Periplus keeps acquisition small and every derived lake write append-only.

## Ingestion

Acquisition stores immutable document bytes before publishing a frozen ingestion job. Ingestion
then inserts the terminal crawl/visit evidence, ordered attempts and steps, and optional document
reference. It never waits for materialization. External HTML enters at the same immutable-byte
boundary.

The five `ingest.*` relations are the complete rebuild authority. Identity replay with the same
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

The current files project structural HTML and JSON-LD at content grain and link occurrences at
visit grain.

A visit batch loads its visits and documents, groups unique HTML content sources, and builds one
shared projection context. Each content body is read and parsed once in that batch. Element zero
in the generation's HTML projection is the durable presence marker for a content identity. If the
marker already exists, no later visit can emit DOM or JSON-LD rows for that content, even when its
`document_id` sorts before the document that first projected it.

For content without a presence marker, the batch containing the lexicographically minimum HTML
`document_id` in the pinned ingestion snapshot owns the initial DOM and JSON-LD output. That
decision is made before workers commit, so parallel batches for genuinely new content select one
owner without racing on a uniqueness constraint. If the owning batch fails, its deterministic
redelivery remains the owner. Every document observation still emits its own link occurrences.

Registry callbacks produce Arrow tables only. Generic lifecycle code validates their schemas,
writes partitioned and sorted final Parquet, registers those files, records progress, and commits the
applied-batch marker. Every preparation gets a fresh immutable file set, so a crash before commit
leaves only unreferenced files; a redelivery after commit reads the marker and is a no-op.

Partition transforms are declared per registry entry; there is no global material partition
policy. Content-owned HTML and JSON-LD currently use eight content-hash buckets because exact
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
3. Horizontally scalable workers append final files and applied markers.
4. Activation checks the exact registry, validates every hidden relation, and catches up visits
   inserted after the pinned snapshot.
5. One DuckLake transaction swaps the complete discovered relation set and writes matching
   generation state.
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
the `web.*` and `content.*` SQL resources and declares their required material relations. This
keeps the runtime API independently evolvable while making installation fail if any dependency of
the four-relation public contract is missing.
