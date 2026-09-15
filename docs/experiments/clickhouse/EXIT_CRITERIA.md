# ClickHouse replacement: implementation exit criteria

This plan is ready to start implementation. The storage and ownership direction is
chosen; several correctness protocols still need implementation evidence. Passing
Compose and DDL checks is not completion. These criteria define the replacement
branch's acceptance, separately from authorization to deploy or dispose of existing
production data.

## First checkpoint: prove one complete vertical slice

Before broad conversion, freeze the typed capture envelope and concrete limits for
headers, redirects, attempts, steps and total message bytes. Specify missing versus
empty values and capture termination/completeness. Then implement one real crawl
through the producer outbox, JetStream, ClickHouse ingestion, HTML/link
materialization and public query readiness.

This checkpoint must establish:

- Exact replay and conflict handling, including a successful insert whose response
  was lost and concurrent consumers processing the same identity.
- A written and tested publication protocol: multi-block/table partial output is
  not exposed as complete, and completion is visible through the actual query route.
- A written retention/replay contract covering queued work, rebuilds, raw readers,
  replica visibility and permanent retirement identities. Destructive retention
  stays disabled until its implementation passes the deletion cases below.

Do not port every projection or build the replacement operator UI before replay
and complete publication pass. If the narrow slice requires a second permanent
workflow ledger or a general multi-engine framework, revisit the design rather
than mechanically expanding it.

## Replacement acceptance

| Area | Required evidence |
| --- | --- |
| End-to-end ingestion | Real low-depth crawls produce immutable visits, ordered attempts/steps, correct retained bytes and queryable material output. Shared acquisitions and reused content retain collection-local lineage and budgets. Header duplicates, redirects, skipped captures and useful HTML after timeout retain their defined evidence. |
| Delivery and recovery | Interrupt before/after object publication, NATS publication, base insert, each material write, completion and ACK. Restart and reconcile. Identical retries produce one logical result; conflicting identity reuse is rejected; no accepted event silently disappears. Permanent failures are inspectable and explicitly retryable or abandoned. Stream-full behavior applies backpressure without losing the producer intent. |
| Customer/control ownership | Completed collections and frozen specifications remain in Postgres after execution cleanup. Final counters and provenance survive. ClickHouse stores result membership; requests, permissions and accounting do not depend on analytical counts. Frontier acquisition/interest limits are enforced. |
| Background rebuild | Serve the old target while rebuilding the affected family and admitting new captures. Include events pending only on ingestion, pending only on materialization, inserted behind a historical scan checkpoint and arriving after scan start. Restart the rebuild, reconcile overlap, prove coverage/completeness, activate and preserve live delivery. Canceling a build releases its consumer/protections explicitly. |
| Public queries | Every existing supported public business case has checked result semantics through the ClickHouse query service. Public versioning stays in `public_v*`; private parser/build identities do not leak into that contract. Timeouts, cancellation, admission, access enforcement and readiness remain functional. Intentional API changes are documented and versioned, not silently dropped. |
| Janitor and retention | Keep customer history while pruning completed execution payloads. Expire one of two collections sharing content: bytes remain. Retire the final protection: logical deletion and eventual raw deletion complete resumably. Active rebuilds, pending ingestion/materialization, unresolved failures and readers block deletion. Stale replicas and unknown ownership never prove absence. Replay cannot resurrect retired evidence. No deletion is authorized by a table-name prefix. |
| Observability | Given a collection, visit or execution ID, an operator can locate its current stage, pending age, failure, retry action and readiness reason. Cleanup reports distinguish candidates, blocked work, requested deletion and completed deletion/bytes. Private query analytics retain access and expiry rules. |
| Persistence | Restart services and restore a backup into an isolated environment. Verify sampled raw hashes, base evidence, lineage, material readiness and resumable control/delivery state together. Define acknowledged-write durability and test the chosen storage/replication configuration; a container restart alone is insufficient. |
| Cleanup | Repository searches and dependency checks find no retired runtime CDC, DuckLake ingestion/materialization path, Parquet registration, global generation lifecycle, collection-history transfer, KV job copies, obsolete API/UI branches or unused environment/configuration. Keep bounded page-local DuckDB where still needed. Update canonical architecture/schema/lifecycle/retention docs and AGENTS instructions with the implementation. Required backend/frontend checks pass. |

## Performance acceptance is a declared workload

Before tuning, record the compute/memory limits, source corpus and content-size
distribution, admitted crawl rate, query concurrency, query cases, per-query latency
and memory budgets, and rebuild resource allocation in the shared benchmark results.
Use the current roughly 200k-capture corpus as a baseline where authorized; preserve
the distinction between visits and distinct contents. Use a dedicated experiment
with homelab-equivalent resource limits, not unrestricted development-machine results
presented as production evidence.

Proposed initial freshness criterion: **p99 at most five minutes from accepted
capture publication to all required public capabilities being query-ready**, under
the declared healthy workload, both with and without a background rebuild. Report
failures/pending captures separately so omitting unfinished work cannot improve the
percentile. Record capture-to-publication delay as well; that boundary must not hide
a producer backlog.

Run a sustained workload for at least 24 hours, including a rebuild interval.
Pass the declared query budgets, enforce process memory limits, and show that live
backlog and pending merge work do not grow without bound. After a bounded injected
outage, demonstrate recovery to the declared freshness target and report drain time.
Record peak storage with old/new material targets and merge headroom.

Perform a scale sweep and report the largest tested corpus, first limiting query
or resource, and measured storage per distinct content/visit. The 200 TB figure is
a cost/capacity reference, not a mandatory cutoff. A billion-content claim requires
corresponding evidence; this implementation is not required to pretend that scale
has been proven. The measured operating envelope becomes the admission limit until
further testing supports expanding it.

## Done means one supported implementation

The branch is complete when every applicable row above has a reproducible command,
fixture or benchmark result and no unresolved correctness gate. Remaining capacity
limits are explicit and enforced. One end-to-end replacement is supported, with
superseded code removed; raw evidence and existing customer data have an explicit
cutover/retention disposition. Production deployment and irreversible data removal
remain separate actions requiring the appropriate authorization.
