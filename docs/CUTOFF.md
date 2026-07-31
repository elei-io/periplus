# Cutoff

This cutoff establishes one immutable lake contract:

```text
crawl plan -> immutable bytes -> ingest.* -> append-only material.* -> web.* / dom.*
```

## Required outcome

Atlas must:

1. Preserve authoritative crawl, visit, attempt, step, and document evidence in insert-only
   `ingest.*`.
2. Verify every visible document object is readable and content-correct.
3. Rebuild exactly three semantic projections:
   - `material.html_elements`
   - `material.jsonld_values`
   - `material.link_occurrences`
4. Append only immutable final files and activate complete registry generations atomically.
5. Recover from retries, restarts, catch-up ingestion, and unreadable material files without
   changing authoritative evidence.
6. Expose registry-derived rebuild, queue, worker, health, API, and UI status.
7. Keep runtime page, latest, link-rollup, URL-component, and DOM-stat semantics in the public
   query layer.

## Direct replacement

Atlas is greenfield. Removed page dimensions, page observations, page heads, content statistics,
and link aggregate tables have no aliases, dual reads, migration bridges, or fallback routes.
Superseded builders, DML, tests, diagnostics, and documentation are deleted with their last caller.

## Materialization boundary

One visit-scoped workload loads a shared parse context and invokes the auto-discovered fixed
projection registry. Each arrow below is currently implemented by one self-contained file:

```text
ingest.visits
    -> optional immutable HTML document
    -> material.html_elements
    -> material.jsonld_values
    -> material.link_occurrences
```

Workers acknowledge only after final file registration and the applied marker commit together.
Rebuild, live CDC, activation, recovery, status, and UI lifecycle iterate the registry; they do not
branch by relation.

Views and macros are excluded from materialization files. The lightweight public-catalogue
registry owns runtime SQL and declares material dependencies separately.

## Current-lake transition

Before activation Atlas records the ingestion snapshot/high-water mark, verifies referenced
objects, stops the old materialization workers, and uses the registry digest to reject
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
- Static tests reject replacement DML against ingestion or semantic material relations.
- Repeated content and parallel batches produce one DOM/JSON-LD projection and distinct
  visit-owned link occurrences.
- Rebuild and live incremental output are logically equal at the same snapshot.
- A low-depth, low-concurrency crawl produces correct ingestion evidence and all applicable
  projections.
- Repository-wide search finds no removed material relation, hard-coded old table list, or
  target-specific material commit branch.
