# Cutoff

This cutoff establishes one immutable lake contract:

```text
shared frontier -> immutable bytes -> ingest.* -> derived material.* -> public_v1.*
```

## Required outcome

Periplus must:

1. Preserve authoritative collection lineage, visit, attempt, step, and document evidence in insert-only
   `ingest.*`.
2. Verify every visible document object is readable and content-correct.
3. Rebuild the registered semantic projections:
   - `material.html_elements`
   - `material.link_occurrences`
4. Append only immutable final files and activate complete registry generations atomically.
5. Recover from retries, restarts, catch-up ingestion, and unreadable material files without
   changing authoritative evidence.
6. Expose registry-derived rebuild, queue, worker, health, API, and UI status.
7. Install the complete public query contract defined in [`SCHEMA.md`](SCHEMA.md).

## Direct replacement

Periplus is greenfield. Removed page dimensions, page observations, page heads, content statistics,
and link aggregate tables have no aliases, dual reads, migration bridges, or fallback routes.
Superseded builders, DML, tests, diagnostics, and documentation are deleted with their last caller.

## Materialization boundary

One visit-scoped workload loads a shared parse context and invokes the auto-discovered fixed
projection registry. Each arrow below is currently implemented by one self-contained file:

```text
ingest.visits
    -> optional immutable HTML document
    -> material.html_elements
    -> material.link_occurrences
```

Workers acknowledge only after the atomic lake append/file registration and
the subsequent control-Postgres receipt both commit. Lost receipts replay safely.
Rebuild, live CDC, activation, recovery, status, and UI lifecycle iterate the registry; they do not
branch by relation.

Views and macros are excluded from materialization files. The lightweight public-catalogue
registry owns runtime SQL and declares material dependencies separately.

## Current-lake transition

Before activation Periplus records the ingestion snapshot/high-water mark, verifies referenced
objects, stops the old materializer replicas, and uses the registry digest to reject
incompatible live work. It rebuilds hidden relations solely from ingestion evidence, catches up
inserted visits, validates counts, identities, joins, and representative public queries, then
activates every discovered relation and the matching public catalogue together. Retired
relations remain until post-activation verification. No ingestion row or immutable object is
deleted or rewritten.

Registered paths must be readable by the configured connection protocol and the supported direct
host shell. Filesystem registrations must not depend on a container-only `/app` path; URI
registrations retain their complete immutable object URI.

## Exit criteria

- Repository checks and catalogue validation pass.
- Ingestion stays append-only; derived publication appends missing deterministic identities without DELETE.
- Repeated content and parallel batches produce one DOM projection and distinct
  visit-owned link occurrences.
- Rebuild and live incremental output are logically equal at the same snapshot.
- A low-depth, low-concurrency crawl produces correct ingestion evidence and all applicable
  projections.
- Repository-wide search finds no removed material relation, hard-coded old table list, or
  target-specific material commit branch.

## Continuous crawler cutoff

[FRONTIER.md](FRONTIER.md) defines the replacement crawl-runtime contract. The implementation uses
one shared frontier, finite collection interests, scheduled ordinary requests, immutable
observations and durable lineage. See [FRONTIER_ACCEPTANCE.md](FRONTIER_ACCEPTANCE.md) for acceptance
evidence and deployment follow-ups. The materialization and immutable-storage boundaries above
continue to apply; no graph compatibility layer is retained.

Request retention uses the existing janitor and catalogue repository. The only new
persistence is exact lifecycle fencing, resumable raw-object retirement receipts,
and temporary publication markers needed by their active ingestion/purge callers.
No separate retention service, queue or Postgres corpus mirror is introduced.
See [RETENTION.md](RETENTION.md).

### Private query execution history

[QUERY_HISTORY.md](QUERY_HISTORY.md) defines the 30-day private `query_executions`
table in control Postgres, bounded best-effort recording, janitor cleanup and the
`observatory/queries` dashboard. This is explicitly approved product analytics;
no query results or crawl history are added to control Postgres.
