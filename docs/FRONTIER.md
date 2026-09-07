# The continuously exploring crawler

Status: destination design, not implemented. This replaces the earlier proposal to add a frontier
while retaining graph runs as the center of execution. The implemented contracts remain in
`ARCHITECTURE.md`, `CRAWL_PLANS.md`, and `LIFECYCLE.md`. This document describes the desired system;
it does not assert that existing tables, APIs, or workers already implement it.

## Purpose

Periplus operates a crawler around the clock to build a queryable web corpus. People guide its
attention through collection requests; they are not its only source of work. Background exploration
continues while eligible links and an operating budget remain. Many workers call the CDP service
in parallel, but users experience one coordinated crawler.

The product story is: watch the web become data, guide where exploration goes next, and query
what arrives. The architecture must make current activity, waiting, cost, and provenance legible.
It must not preserve historical graph machinery merely because it already exists.

## Core concepts

| Concept | Responsibility |
| --- | --- |
| Collection request | Intent, SQL selection rules, scope, budget, priority, visibility, and completion outcome |
| Frontier | Authoritative pending acquisitions, eligibility, selection reasons, and scheduling state |
| Acquisition | One physical page capture with defined requirements; may serve several compatible requests |
| Observation | Immutable evidence of that capture, independent of which requests benefited |
| Selection policy | Determines which starting or discovered URLs qualify, including background exploration |

A request's interest in a URL is distinct from the physical acquisition. That association carries
request-specific traversal context and accounting. These are responsibilities, not a mandate for
one service or table per concept. There is one authoritative frontier, not a second queue ledger
layered over current crawl requests.

```text
collection requests -> seed selection ----+
                                         v
public discovery -> background policy -> shared frontier -> bounded dispatch -> CDP acquisition
                                         ^                                      |
                                         |                         +------------+------------+
                                         |                         v                         v
                                         +--- SQL link selection <- navigation          immutable evidence
                                                                                              |
                                                                                 ingestion -> materialization
                                                                                              |
                                                                                        public SQL
```

Graphs are not a required core primitive. Seed selection plus follow-link SQL, depth context, and
limits are the initial traversal model. Retain arbitrary graph execution only if a concrete
workflow demonstrates a need that this model cannot express. Do not implement a synthetic graph
run for every background page or wrap the continuous crawler in one immortal run.

## Requests and background exploration

Requests are queued for resolution/admission and can be prioritized. They may admit work
incrementally; a request is not a single FIFO position. URL input, text source discovery, and SQL
seed selection all produce candidates for the same admission path. The simple public form maps
to supported SQL selection and explicit limits, rather than a separate traversal implementation.

Background exploration independently selects new public links, subject to deduplication, domain
and path restrictions, crawl-trap controls, and a bounded operating budget. It receives a configurable
capacity share, can use spare capacity, and can be slowed or disabled by setting its allowance to
zero. Requests get preferential service, with aging and explicit allocation to prevent starvation.
The scheduler may idle when nothing is eligible; continuity does not justify ignoring constraints.

A request's depth and page budget bound work attributed to that request. Background exploration
may independently continue beyond its boundary under the background policy, without charging that
work to the request or making its completion wait. Global exclusions still apply. Private collection
must not seed public background exploration. Every background acquisition has a selection reason,
policy version, and parent observation where applicable; no artificial user request is necessary.

Initial background policy favors eligible unseen URLs with bounded domain diversity and explicit
rate/spend limits. Control calendars, session variants, faceted navigation, and infinite link spaces.
Keep URL identity conservative: fragments can be normalized away, but query parameters cannot be
indiscriminately stripped. Refresh of previously observed pages is a later explicit policy with
freshness and revisit rules, not accidental rediscovery on every link or an unconditional recrawl.
The implementation must define bounded discovery checks against durable evidence and pending work
before enabling background exploration; deleting operational state must not make every URL unseen.

## Sharing acquisition and preserving lineage

Compatible queued acquisitions are shared from the first frontier implementation. Admission
atomically attaches a request's interest to matching pending work or creates an acquisition.
Equivalence includes normalized URL, effective capture requirements, and visibility/access context;
URL equality alone is insufficient. Initially only compatible public requests share. No private
cross-context sharing is implied. Required capture parameters are frozen before dispatch.

One acquisition produces one observation, not one fabricated visit per requesting collection.
Each attached request receives the result and applies its own follow-link SQL and depth/scope
context. Fetch sharing never merges traversal permissions. A selected page consumes one page-budget
unit per participating request; retries do not consume another unit. Report pages supplied to a
request separately from physical acquisitions and browser cost. Page-budget accounting is not a
future billing policy.

Cancellation detaches that request's outstanding interest. Shared work continues if another request
or an independently admitted background reason still needs it. Completion notification and admission
are retry-safe; concurrent selection cannot exceed budgets or create duplicate physical work for
the same pending acquisition identity.

Initially freeze participants at dispatch: new requests do not attach to an in-flight acquisition.
A later request can schedule subsequent work. Reusing historical captures requires a separate,
explicit freshness contract and is deferred. This limited boundary avoids pretending all temporal
requests are equivalent while removing duplication in the pending frontier.

Requests anchor the user-facing reason for collection; observations anchor captured evidence.
Persist the relationship and selection provenance in DuckLake so it survives operational cleanup.
Support request -> observations and observation -> requesting collections, parent evidence, and
selection rule. Background reasons fit the same provenance contract without a request ID. Retain
only the lineage needed to explain actual decisions, not an unbounded record of every rejected link.

The current run-owned visit/crawl relationships are not assumed compatible with this model. Design
an explicit evidence contract for shared acquisition and request fulfillment before implementation;
replace obsolete ownership relationships rather than invent duplicate visits or dual-write lineage.
Preserve existing immutable evidence. Any historical transition requires an explicit decision;
this design does not authorize rewriting lake history or introduce a compatibility bridge.

## Admission, eligibility, priority, and capacity

These are separate decisions:

- Admission: accept eligible intent within request and aggregate frontier budgets, durably.
- Eligibility: determine whether queued work may run now, considering pauses, timing, retry
  backoff, exclusions, website pacing, and dependency health.
- Priority: choose among eligible work using request importance, background allocation, domain
  diversity, and aging. Future payment, novelty, freshness, PageRank, or model scores can inform it.
- Capacity: bound concurrent dispatch, browser use, object I/O, and operating cost.

Priority never overrides website rules, hard budgets, or access restrictions. Keep fair selection
across requests and domains; a slow domain must not occupy the entire dispatch window. Prevent a
single large request from monopolizing the crawler. Shared work must not multiply its scheduling
weight merely because the same interest was submitted repeatedly.

Durable admission does not wait for a browser slot. Once a page's selected links are durably
accounted for, its link processing finishes, independently of downstream acquisition. Deduplication,
budget consumption, and selection checkpoints must be safe under retries and concurrent workers.

Bound stored frontier size separately from active delivery. At genuine admission capacity, retain
requested selections with an explicit waiting reason and bounded resumption; never silently mark
them handled. Background selection may decline candidates under its documented budget. Do not
recreate the current 48 queued-plus-fetching ceiling under a new name or retry every blocked edge
every second. Recovery checkpoints remain necessary; acquisition-capacity polling does not.

## Runtime ownership and recovery

Periplus Postgres owns editable intent, policies, the current frontier, request interests, counters,
and transactional dispatch outbox. NATS owns bounded delivery, worker presence, execution leases,
and per-domain permits. It is not another authoritative frontier. DuckLake owns durable evidence
and historical provenance; immutable objects remain content-addressed.

Keep the existing crawler process role and standard CDP acquisition boundary unless measured needs
justify a topology change. Distributed dispatch uses transactional claims and recoverable outbox
publication. No database lock spans remote I/O. Recheck cancellation, pauses, and eligibility before
starting delayed deliveries. Expiring claims and idempotent outcomes recover work after crashes.
Retry limits distinguish temporary acquisition failures from definitive outcomes; provider/storage
outages should pause affected work or back off rather than burn the entire budget.

Ingestion and materialization remain independent of traversal. Capturing, ingesting, and becoming
structurally queryable are different milestones. No catalogue query runs implicitly in every link
selection. Python owns logic and status contracts; Next.js proxies APIs and renders the experience.
No new scheduler service, generic locking system, event store, or plugin framework is required.

## SQL selection and future intelligence

Two explicit SQL capabilities serve different purposes:

1. Corpus selection queries existing evidence to identify gaps and produce bounded starting URLs.
   Run it on explicit submission or later on a schedule, through the bounded read-only query
   boundary. Record query, parameters, and source snapshot context where available; freeze results
   before admission so retries do not silently change the selection.
2. Follow-link selection runs bounded SQL over the newly acquired page's navigation package.
   It chooses candidates using request-specific traversal context. Historical catalogue joins do
   not become a per-page acquisition dependency.

The public form and operator-authored SQL use these same contracts. Depth and scope are policy
inputs, not justification for a mandatory graph engine. SQL cannot bypass budget, visibility,
URL validation, egress protections, or domain rules.

Future intelligence has specific locations: discovery proposes seeds, link selection can consume
scores/classifications, and scheduling can rank eligible work or initiate refresh. Introduce batched
scoring only for an actual caller. Record model/policy versions and retained decisions when used.
An AI recommendation must not change hard limits or evidence identity. No ML system is needed for
the first fair scheduler.

## Dynamic control and capture quality

Operators need global and per-domain pause/resume, concurrency and pacing controls, request
priority, background allocation, exclusions, and browser-time/spend ceilings. Requested speed is
an upper bound, not promised throughput. Show configured limits alongside measured activity.
Changes apply to queued/future work at eligibility checks; in-flight captures normally finish under
their frozen settings. Emergency stop behavior must be explicit. Attribute policy changes and show
which effective version affected a decision.

Unknown domains/paths start with a conservative, thorough capture profile, including bounded
scrolling where appropriate. “Scroll to bottom” is not proof of completeness: infinite scroll,
blocked pages, and partial rendering require time, byte, and interaction stopping limits. Periplus
owns completeness requirements and evidence correctness; the CDP service executes supported
transport/browser strategies. Do not duplicate a browser strategy engine in the frontier.

Record the effective capture strategy/version, elapsed browser time, response outcome, and available
quality signals. Distinguish technical success from useful content: a 200 response can contain a
challenge or an empty shell. Later domain/path experiments can compare lighter captures with a
small conservative reference sample, promote only under explicit quality/cost criteria, detect
regressions, and revert. Experimental assignment must participate in acquisition equivalence so
sharing cannot contaminate comparison groups. Experiments and automatic promotion are deferred;
the initial contract retains sufficient evidence to evaluate them later.

Adaptive controls may eventually slow domains for error/quality deterioration. Keep measured
signals distinct from estimates, and require bounded adjustments and a manual override. Better
quality measurements should improve efficiency without interpreting what the public corpus ought
to mean or replacing immutable captured evidence.

## Live visibility and request transparency

The public Live route starts with bounded polling. Show current domains, the latest five successful
public captures, and a small upcoming preview with an “as of” time. Multiple workers have multiple
locations. Upcoming work is a scheduling estimate and can change, not a reserved global FIFO order.
Show global and per-domain acquisition velocity over explicit intervals, active work, queue age,
errors, and later quality/cost signals. Distinguish physical fetch rate from request fulfillment.
There is no overall completion percentage for a continuously growing corpus.

A request view links to its frontier items and arrivals; public frontier items and observations
link back to their public requesting collections or background selection reason. Shared acquisitions
can have several callers. Enforce visibility in both directions, including counts and scheduling
explanations. Never expose private targets, credentials in URLs, provider secrets, or private SQL.

| Request situation | User-visible explanation |
| --- | --- |
| Resolving | Finding starting URLs or evaluating seed SQL; separate from queue waiting |
| Awaiting admission | Which work is not yet in the frontier, why, and elapsed wait |
| Queued | Admitted pages, runnable/deferred counts, oldest wait, and next-dispatch constraint |
| Collecting | Fetching, supplied pages, failures, shared acquisitions, recent arrivals, last progress |
| Settled | Budget reached, eligible links exhausted, deadline, cancellation, or failure; query readiness separately |

A request may span several stages at once. Explain partial admission, continuing discovery, and
mixed domain delays. A budget is “up to N pages,” not a completion target guaranteed to exist.
Background continuation does not keep a completed request open. Request completion requires its
own selection/fulfillment work to settle, not an empty frontier or completed materialization.

Provide admission and next-start estimate ranges when observations support them, with calculation
time and uncertainty; otherwise give a specific unavailable reason. Known eligibility time is not
promised dispatch time. Completion estimates may be unavailable while discovery expands. Never
invent a global queue number; scoped ordering is shown only when the scheduler guarantees it.
Expose stale status explicitly and explain pacing, backoff, capacity, pause, and admission limits.

Use bounded current-state reads for active work and retained lake evidence for historical arrivals.
A capture is marked queryable only when that milestone is verified. Polling must not execute work
or provision NATS consumers. No duplicate historical telemetry database or prediction service is
needed for the initial experience.

## Replacement scope and delivery

Before runtime implementation, walk through overlapping requests, divergent SQL rules, cancellation,
background continuation, failure/restart, and later refresh using this model. Compare a replacement
of the scheduling core with adopting an established frontier implementation. Evaluate integration
cost and operational simplicity; neither preserving existing code nor replacing it is a goal in
itself. Resolve acquisition/lineage schema and atomic dispatch details before writing a migration.

| Retire or reconsider | Destination |
| --- | --- |
| Independent graph runs as the crawler's organizing principle | Continuous shared frontier with finite collection interests |
| Mandatory nodes/edges and synthetic depth graphs | Seed SQL, follow-link SQL, and explicit traversal context |
| One run owns each physical capture | Compatible queued work shares one acquisition and observation |
| Request ancestry required for every fetch | Explicit request, background, and later refresh reasons |
| Per-run 48 queued-plus-fetching ceiling | Separate frontier admission budget and bounded dispatch |
| Edge retries waiting for acquisition slots | Durable selection accounting independent of dispatch availability |
| Ambiguous graph-completion progress | Acquisition, fulfillment, selection, and query readiness explained separately |

Keep immutable storage, the CDP boundary, bounded SQL execution, domain politeness, and independent
lake processing where they fit. Replace obsolete graph execution, ownership fields, delivery paths,
APIs, counters, UI concepts, and tests with their last caller; do not add compatibility aliases,
parallel schedulers, dual evidence writes, or a second frontier ledger. Adapt documentation and
AGENTS.md to the implemented architecture at cutover. Existing graph docs remain factual until then.
Use Alembic for operational schema changes and a coordinated drain or explicitly authorized reset
of disposable runtime state. Preserve durable observations; no implicit historical reset is allowed.

The first slice includes compatible queued sharing, bounded background exploration with a zero
setting, simple fair scheduling, SQL seed/follow selection, and honest public/request visibility.
Defer in-flight joining, historical-result reuse, automatic refresh, paid billing, learned ranking,
capture A/B automation, and arbitrary graph orchestration absent a demonstrated caller.

## Acceptance criteria

- Two compatible queued requests cause one physical acquisition and one observation, both receive
  fulfillment, and each applies its own traversal rules and budgets. Incompatible captures do not
  merge. Cancelling one caller does not strand the other.
- Background exploration continues independently after a public request finishes, respects its own
  rate/spend/domain limits, stops at zero allocation, avoids trap expansion, and cannot inherit
  private discoveries. Restart does not reset seen-URL eligibility.
- Busy browsers do not prevent bounded durable admission. Fair scheduling progresses eligible
  work across requests and domains without unbounded local or delivery queues.
- Retries, concurrent admission, crashes at publication/commit, pause, cancellation, and deadlines
  neither lose accepted work nor double-charge budgets or fabricate duplicate observations.
- SQL seed retries preserve selected inputs; follow-link selection stays page-local and respects
  request-specific context. The public form uses the same contracts as operator requests.
- Lineage explains each acquisition and survives operational cleanup. Physical acquisition counts
  differ correctly from pages supplied across overlapping requests.
- Live and request views show partial admission, waiting reasons, truthful estimates, shared
  provenance, stale dependencies, and verified query readiness without leaking non-public work.
- A low-depth, low-concurrency collection reaches correct ingestion and materialization. Throughput
  validation measures successful acquisitions and browser cost, not merely terminal request counts.
- Superseded graph/scheduling paths and contracts are deleted at implementation, with no permanent
  compatibility layer. Architecture, schema, lifecycle, deployment, and API docs describe one system.
