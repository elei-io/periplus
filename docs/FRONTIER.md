# Continuous crawl frontier

Status: accepted destination architecture; not yet implemented. This document governs the next
crawl-runtime change. Existing operational behavior remains documented in `CRAWL_PLANS.md` and
`ARCHITECTURE.md` until that change lands. Implementation must replace superseded behavior and
update those contracts in the same change, rather than leave two scheduling paths.

## Product and execution model

Periplus continuously builds a queryable web corpus. Public coverage requests guide where it
explores; operator plans provide precise SQL-directed traversal. One logical frontier coordinates
eligible work from all requests and plans, served by many symmetric crawler replicas. It is not
one process, one browser, or one never-ending graph run. Finite runs retain their own scope,
budget, deadline, and completion; new work can arrive while other runs finish.

The evidence path remains:

```text
coverage intent / operator plan
    -> frozen run and starting URLs
    -> durable frontier admission
    -> bounded, fair dispatch -> CDP acquisition -> immutable bytes -> ingestion -> materialization
                                      |
                                      -> page navigation -> SQL selection -> frontier admission
```

Selection decides which URLs qualify. Scheduling decides when eligible requests execute.
Acquisition captures one page. None of these waits for catalogue ingestion or materialization.

## Authorities and ownership

- Periplus Postgres remains authoritative for current runs, queued requests, eligibility,
  admission deduplication, edge progress, budgets, and the transactional outbox. Existing crawl
  request records are the frontier; do not add a second URL ledger.
- NATS remains bounded work delivery, worker presence, scoped execution leases, and shared
  per-domain pacing. A delivered message is not a second authoritative request state.
- Crawler replicas own dispatch participation, one-page acquisition, navigation preparation,
  and scoped SQL edge evaluation. Scheduling belongs in `crawl/runtime/`, composed by the
  existing crawler role; this change adds no scheduler service or acquisition queue.
- The CDP service owns browser/transport capacity. Periplus retains acquisition correctness and
  website politeness. Domain permits and operation leases retain their distinct purposes.
- Immutable objects and DuckLake remain evidence authorities. Postgres frontier state is current
  operational state, not a new historical crawl store.
- Python owns admission, scheduling, counters, and safe public activity contracts. Next.js proxies
  those contracts and renders the experience.

## Admission is separate from dispatch

A selected URL is durably admitted when it passes the frozen run's constraints and deduplication
and consumes the applicable run budget. Admission does not require a free browser or an empty
acquisition delivery slot. The first implementation preserves normalized-URL deduplication within
one run; it does not share acquisitions across runs or broaden one request's scope using another.
Retain run, node, parent-page, source-edge, and effective-policy provenance.

Admission, budget accounting, and edge checkpoints must be transactionally retry-safe. Concurrent
edges cannot exceed the run budget. A parent finishes traversal once every outgoing selection is
durably admitted or definitively handled as duplicate, out of scope, or over budget. It does not
wait for children to fetch. Leaves skip navigation work. A run completes only when its own
outstanding requests and traversal work settle, not when the global frontier is empty.

The frontier is bounded separately from delivery: retain per-run budgets and define an aggregate
operational admission bound before enabling unlimited submission. At that bound, defer admission
with an explicit reason and bounded retry; do not silently discard selections or mark the parent
complete. This exceptional storage backpressure must not reuse the worker dispatch window.

Dispatch considers eligible queued records and makes only a bounded amount of acquisition work
available to workers. Capacity bounds protect delivery, local memory, browsers, and object clients;
they do not limit how many valid URLs can be durably waiting. Multiple replicas claim dispatch
work safely through current-state transactions and the existing outbox. Never publish the entire
frontier into an unbounded local or JetStream in-flight backlog.

Use fair selection across runs and runnable domains, with aging so old eligible work cannot starve.
Slow or paced domains must not occupy every dispatch slot. Respect `not_before`, run pause,
cancellation, deadlines, and shared domain permits before acquisition; recheck authoritative state
when consuming delayed work. A dispatch claim must not hold a database transaction or advisory
lock across CDP, NATS, or object-store I/O. Recovery reconciles interrupted claims and outbox work;
redelivery must neither duplicate budget consumption nor lose admitted URLs.

## Direct replacements

| Existing behavior | Replacement |
| --- | --- |
| Per-run 48-request queued-plus-fetching admission ceiling | Separate durable frontier budget and bounded acquisition dispatch |
| Parent edge waits for children to free acquisition capacity | Parent settles after durable selection accounting |
| Repeated one-second edge redelivery while that ceiling is full | Normal selections complete admission; genuine deferrals have an explicit reason and bounded retry |
| Per-run acquisition windows implicitly determine which work is visible | Shared fair selection over eligible queued requests, preserving run ownership |
| “Processing links” combines computation and capacity waiting | Separate navigation work and explicit waiting reasons |
| Aggregate acquisition-settled count used as apparent successful progress | Successful acquisition, terminal failure/cancellation, and queryability reported distinctly |

Delete the superseded admission constant/check, acquisition-capacity deferral path, and tests that
encode that coupling when dispatch replaces them. Retain checkpoints needed for crash recovery
and real admission backpressure. Update APIs, types, UI, tests, and documentation together; do not
keep aliases, dual counters with conflicting meanings, compatibility routes, or parallel schedulers.
Schema changes use Alembic. Drain or explicitly reset affected disposable development runs at
cutover; never silently reinterpret frozen in-flight work, and never reset historical lake evidence.

## Public live experience and observability

The public story is “guide exploration, watch new evidence arrive, explore the resulting data.”
A bounded polling endpoint and a public Live route are sufficient initially. Show:

- Domains currently being acquired, rather than a fictitious single crawler location.
- The latest five successful public arrivals, with available title, domain, observation time,
  and a link to explore the evidence when queryable.
- Successful acquisitions over a stated interval, active fetching, waiting work, and recent errors.
- Public coverage requests and their individual progress and last meaningful activity.

Publish only explicitly public activity. Establish that visibility boundary before exposing a
cross-run feed; private targets, URL query secrets, raw provider errors, and future enterprise
plan SQL must not leak. This does not require building enterprise tenancy now.

Operational states must distinguish waiting to fetch, fetching, navigation evaluation, and terminal
outcomes. Waiting exposes reasons such as pacing, retry backoff, dispatch capacity, or admission
budget. Successful acquisition is separate from ingestion and structural query readiness. Never
infer “queryable” from acquisition or run completion. Counts must state their grain and whether
they overlap; the continuous corpus has rates and queue ages, not a completion percentage.

Use current runtime state for active work and durable lake evidence for retained historical
arrivals. Keep activity reads bounded and independent of work execution; polling must not provision
NATS consumers or trigger crawl work. Expose update times and stale/unavailable states instead of
inventing activity. Do not create a second historical event store for the feed.

## SQL traversal and future steering

Keep frozen SQL edges over the acquired page's bounded navigation package. Public depth/scope
forms compile to ordinary plans; operators can author precise SQL plans. Frontier scheduling does
not replace these plans, add historical joins to edges, or introduce new acquisition primitives.

There are three future steering boundaries: source discovery proposes starts; selection scores or
classifies page links; scheduling prioritizes eligible work or later refreshes. A concrete ML caller
may introduce batched scores and decision provenance (model/version, inputs or evidence references,
and retained outputs). SQL can select using explicitly supplied scores once its contract is designed.
Models never bypass scope, budget, pacing, or visibility. No plugin framework, compulsory per-URL
model call, score schema, or separate ML service is part of the first implementation.

Defer cross-run acquisition sharing, automatic refresh policies, learned ranking, and enterprise
isolation machinery. Sharing will require explicit freshness and acquisition-equivalence rules;
keep provenance now without pretending matching URL strings are sufficient.

## First implementation acceptance

1. A bounded multi-page run can admit eligible links while workers are busy; its parent edges do
   not retry merely because the dispatch window is full.
2. Two competing runs and multiple domains make progress with bounded delivery and memory;
   paced domains do not starve runnable ones. Measure queue age as well as throughput.
3. Parallel admission never exceeds budgets; retries and crashes lose no admitted work and do
   not duplicate evidence or consume budget twice.
4. Pause, cancellation, deadlines, duplicate links, and restart during dispatch/edge checkpointing
   settle correctly. Collection completion remains independent of materialization readiness.
5. A low-depth, low-concurrency crawl produces correct lake evidence; compare successful
   acquisitions per minute separately from terminal graph-request completions.
6. Public activity excludes non-public work, distinguishes captured from queryable, remains
   bounded under polling, and reports stale dependencies honestly.
7. Superseded admission/deferral code and terminology are removed with the implementation;
   architecture, lifecycle, deployment, and API documentation describe one actual execution path.
