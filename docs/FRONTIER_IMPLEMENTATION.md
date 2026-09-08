# Frontier implementation ledger

> Historical frontier design/verification record. Its independent background selection,
> allocation and seen-check paths were removed on 2026-09-08. Current execution ownership
> is described in [ARCHITECTURE.md](ARCHITECTURE.md); recurring exploration uses ordinary
> requests via [SCHEDULES.md](SCHEDULES.md). Background-specific acceptance below records
> the superseded implementation, not current requirements.

Target: all of [FRONTIER.md](FRONTIER.md), including its acceptance criteria. This ledger tracks
implementation and verification; it does not narrow the definition of completion.

## Current status

The continuous frontier, collection API, public/admin applications, SDK, and greenfield schema have
been cut over and deployed locally. Disposable control, delivery, lake, and object state was reset
in a coordinated cutover. The old graph execution paths have been removed. The core cutover has been deployed to the API, query service, crawler, ingestors, materializers, and
janitor. Subsequent image deployments and health checks are recorded below. Current architecture and contracts are in ARCHITECTURE.md, SCHEMA.md,
LIFECYCLE.md, QUERY.md, and DEPLOYMENT.md.

Live acceptance has verified one-page capture through query readiness, compatible queued sharing,
recent reuse, private isolation, cancellation of one queued participant, delayed ingestion and
materialization with restart, durable provenance after operational cleanup, and background
continuation with zero-allocation control. The latest full check passed 499 backend tests (31
opt-in skips), 23 SDK tests, and both frontend checks/builds. These results do not prove every
remaining requirement.

The final implementation/acceptance audit is complete. The scraper VLAN remains a user-owned
deployment follow-up; provider-wide network isolation is not claimed as verified. The scheduler
alternative comparison was documented retrospectively, so its requested pre-implementation timing
was missed. Background allocation remains zero outside explicitly bounded experiments.

The entries below are chronological implementation history. Early statements about missing code,
old schemas, or undeployed images describe the state at that step, not the current deployment.
Later entries record their resolution and authoritative acceptance evidence.

## Initial implementation evidence

- The crawler entrypoint now runs the assembled frontier dispatch, recovery, selection, outbox,
  and bounded capture loops. The old crawler's graph dispatch buffer, navigation consumer, and
  early evidence publication have been removed, along with their obsolete buffer tests. Startup
  validates the frontier schema before connecting delivery or CDP. NATS capture, domain permits,
  operation leases, and crawler presence are reconciled at startup. Policy resolution uses control
  policies directly. Seed SQL uses the process-owned query client; provider-wide
  dependency controls and schema cutover remain.
- Acquisition input is now an explicit typed `AcquisitionContext`, with no graph IDs or ambient context.
  `VisitRecord`, physical visits, and `web.observation` have no `crawl_id`; public catalogue version is
  2.0.0. Import constructors and public-view fixtures use this direct contract. The existing local lake
  has NOT been reset/upgraded: do not deploy the incompatible source schema before coordinated cutover.
  Terminal `CrawlRecord` and its graph/import grouping callers still need replacement by lineage.
- Local control Postgres is reachable. Tests use isolated temporary schemas, leaving application
  state intact. Compose also has obsolete services; cutover must remove old producers and deliveries.
- The replacement store is wired into crawler source startup but not deployed. It currently
  implements request URL deduplication, queued sharing, recent-result lookup, page reservations,
  cancellation, dispatch fencing, outcome acceptance, and bounded uncertain-attempt recovery.
- `test_frontier_store.py`: 37 passing behavior tests, including splitting a paused caller from
  shared queued work, outbox claim expiry/backoff, and collection settlement independent of unrelated
  frontier work. Seed/link selection now freezes bounded candidates and resumes a durable cursor.
  Admission capacity retains remaining work; completion rejects unaccounted URLs unless the request
  hits its own hard budget or stops. Tests cover query-once resumption, competing frozen selections,
  divergent follow rules on a shared capture, and deadline settlement without running seed SQL.
- `frontier_selection.py` processes at most 64 admissions per pass and never waits for child capture.
  Seed SQL is now wired to the read-only query service with frozen positional parameters,
  snapshot, query ID, and selection time. The service pins its transaction snapshot before binding;
  a real DuckLake two-transaction test verifies visibility across a concurrent commit. Collection
  outcomes retain seed provenance and candidate digests through ordinary lineage ingestion. Text discovery now uses bounded durable phases described below. Selection scheduling/backoff runs in the crawler. Page-local follow SQL
  uses an independent locked DuckDB connection with a 128 MiB memory limit, five-second interrupt,
  1,000 URL/2 MiB output bounds, and no external/table-function/catalogue access. Four SQL tests
  cover filtering/deduplication, boundary rejection, output-limit failure, and invalid syntax/binding.
- `test_acquisition_boundary.py` verifies capture returns frozen observation/document evidence with
  no graph owner and does not publish ingestion. Frontier completion rejects evidence with another
  acquisition ID and conflicting navigation on replay.
- `frontier_capture.py` now handles one delivery using operation and domain leases, a renewed
  Postgres claim, acquisition-owned immutable bytes, optional navigation, and fenced outcome
  acceptance before ACK. Three handler tests cover acceptance ordering, stale-delivery suppression,
  and unknown attempt timing. The crawler source entrypoint now invokes this handler through bounded pull lanes.
- Known retry failures retain their attempt/step evidence and enter deferred pending work without
  consuming another page unit or holding dispatch capacity. The scheduler redispatches with a new
  generation and preserves frozen participants. Cancelling the last retry interest emits terminal
  evidence and reclaims its slot. Delayed delivery rechecks deadlines. Exhausted crash recovery emits
  failed evidence with explicit uncertain attempts whose finish times remain null. Prior completion
  steps are retained across attempts. Provider-wide outage controls and physical cost budgets remain.
- `PERIPLUS_TEST_FRONTIER_POSTGRES=1 uv run python -m unittest discover -s tests -p
  test_frontier_postgres.py`: five passing Postgres tests, including concurrent sharing, repeated
  cancellation/dispatch races, and disjoint outbox batches claimed by parallel publishers. Temporary
  schemas are removed by test teardown. Concurrent schedulers also select distinct acquisitions with
  consistent admission/active counters. Selection workers claim distinct collections concurrently.
- Request scheduling uses a bounded priority offset and durable service turns. New requests enter
  at the current turn; queued URL count does not multiply weight. A grouped bounded candidate query
  excludes deferred/saturated domains before its limit. Store transitions also enforce domain dispatch
  ceilings; NATS remains authoritative for actual website pacing. Tests cover a 70-URL slow domain,
  collection rotation, delay expiry, and lower-priority progress. Background scheduling remains undone.
- `frontier_queue.py` owns startup reconciliation of one bounded file-backed capture stream and
  explicit-ACK consumer. At stream capacity it rejects new delivery rather than discarding accepted
  work. Three queue tests cover creation, read-only reuse, and contract drift; live startup remains to be verified at cutover.
- Immutable collection definitions/outcomes, fulfillment, and acquisition reasons now have typed
  records, physical lake relations, ordinary ingestion jobs, replay/conflict checks, and frontier
  transactional outbox producers. Public `web.collection`, `web.fulfillment`, and
  `web.acquisition_reason` views preserve their separate grains and filter private lineage.
- `test_lineage_ingestion.py`: three real temporary-DuckLake tests cover append/replay, independent
  collection definition/outcome identities, late reuse without observation duplication, conflict
  rejection, and public filtering. `test_frontier_outbox.py`: three relay tests cover PubACK ordering,
  failure release, and routing lineage to the existing ingestion lane.
- Outbox claims use short `SKIP LOCKED` transactions, expiring tokens, and bounded retry delays.
  Publication marking requires the current unexpired token; published records are never reclaimed.
  The relay runs in the crawler task group; accepted observation and lineage jobs use the same ingestion lane.
- Collection selection has expiring claims, durable resumption times, and bounded dependency
  backoff. One pass admits at most 64 URLs; seeds settle before follow-link work is selected.
  Paused deadlines are enforced, capacity waits resume after 15 seconds, and idle/paused work does
  not spin. Cancellation drains a synchronous selection pass before releasing its claim.
- Two assembled-runtime tests exercise seed admission through accepted capture, selection settlement,
  observation/lineage relay, and paused deadline settlement with real repository transitions and
  simulated remote I/O. A startup test proves missing schema fails before NATS acquisition.
- Verification in this continuation: 49 frontier tests pass (four opt-in Postgres skips at that run);
  five live Postgres tests pass separately, including the new concurrent selection claim test.
  `make check` passes after entrypoint replacement: 331 backend tests (five opt-in Postgres
  tests skipped there), 11 SDK tests, package checks, and both frontend typechecks/builds.
  The final follow-SQL error classification change passes all four targeted SQL tests separately.
  Malformed or unbindable follow SQL is a terminal collection error rather than an endless
  dependency retry. These checks do not prove remaining acceptance criteria or deployed cutover.

## Required work and proof

| Requirement | Remaining implementation and authoritative verification |
| --- | --- |
| One shared frontier | Complete collection controls/live views and live crawler/query validation; Alembic cutoff removes graph tables; search all callers and remove obsolete graph runtime, queue, plans, tests, and UI |
| Frozen finite collections | Finish seed URL/text/SQL resolution, immutable checkpoints, page-local follow SQL, bounded resumption, completion/deadline semantics; test duplicate/context races and restart |
| Sharing and reuse | Connect immutable acquisition result and navigation retention; test divergent selection rules, age, incompatible capture, cancellation, and pinned eviction |
| Fair bounded dispatch | Implement domain/request fairness, aging, background allocation, and current domain controls; load-test without a large request or slow domain monopolizing delivery |
| Cost and velocity controls | Bound each physical attempt; account uncertain cost; global/domain pause and speed controls; recheck delayed deliveries; verify measured versus configured velocity |
| Background exploration | Implement batch seen checks, ingestion gap protection, check/cleanup watermark, unique background URL admission, trap restrictions, bounded candidates, zero allowance; test restart and lake outage |
| Durable frozen outcome | Replace graph context and early ingestion publication; ordinary outbox with claim/publish/ACK recovery; crash-point tests from bytes through accepted result and ingestion |
| Evidence and lineage | Replace crawl ownership with observations, collection definitions/outcomes, fulfillments, and reasons in ingestion/lake/public registry; test replay/conflict and late reuse without reprojecting content |
| SQL selection | Preserve public bounded query boundary, snapshot/result freezing, navigation-only follow queries, Python scope/egress validation; test retries and independent selection |
| Observability | Bounded live/history/next estimates, waiting explanations, physical versus fulfillment counts, verified query readiness, visibility-safe provenance; inspect real API responses and frontend rendering |
| Public and admin | Read local AGENTS.md before edits; replace graph concepts and shared API types, use React Query/shadcn and error toasts; both typechecks and builds |
| SDK and shell | Replace graph submission/tracking contracts, examples, readiness tests; no compatibility aliases |
| Coordinated cutover | Stop old workers/producers; reset authorized disposable data consistently; initialize one replacement system; update architecture/schema/lifecycle/query/deployment/AGENTS docs |
| Verification | Required make check; catalogue validation; low-depth/concurrency live collection through CDP, ingestion, and materialization; observe background continuation and zero stop; measure useful capture rate and browser cost |

## Next implementation concerns

- Store methods currently use one short locked crawler-control row for exact capacity counters. No
  lock spans remote I/O. Measure transaction contention before choosing more elaborate allocation.
- Paused participants move into subsequent queued work without consuming their budgets. Verify the
  worker's delayed-delivery pause/deadline handling as part of composition, not only at dispatch.
- Dispatch recovery creates terminal evidence on attempt exhaustion and returns other expired work
  through normal rate/pause eligibility. Bounded recovery scans now run in the crawler task group.
  Repository and simulated-I/O runtime tests do not prove real CDP/NATS worker recovery.
- Add bounded interest/request/input capacity as well as acquisition counts, and close typed outcome,
  allowed-section validation, deadline, exclusion, and startup-schema contracts before wiring ingress.
- Do not install the unfinished frontier alongside the graph scheduler or expose parallel APIs. The
  source can be assembled and tested incrementally, but runtime cutover replaces the old path.

## Latest seed-query integration

- The crawler uses one bounded process-owned HTTP client; it closes after selection work drains.
  Missing credentials, service saturation, malformed upstream envelopes, and transport/storage
  outages defer selection. Invalid SQL, explicit query limits, truncation, and invalid URL results
  fail the collection. HTTP redirects are not followed with the query credential.
- Intent is bounded to 256 KiB, and seed SQL/parameters to a 120 KiB request envelope. Six client
  tests cover parameters and provenance, invalid/truncated results, operational failures, oversized
  responses, absent credentials, and intent limits. The frozen-checkpoint test also verifies
  parameters are passed once and snapshot/query/time provenance survives resumption.
- Real DuckLake lineage append/replay tests retain nested seed provenance, including its timestamp.
  Query-service tests cover transaction snapshot consistency as well as read-only execution.
- `helm lint`, `helm template`, and `docker compose config --quiet` pass for the crawler query-client
  environment wiring. No services have been restarted and no data has been reset.
- Full `make check` passes for this integration: 339 backend tests (five opt-in Postgres
  skips), 11 SDK tests, package checks, and both frontend typechecks/builds. The temporary-lake
  snapshot test proves transaction consistency; real deployed query/CDP integration remains unverified.

## Latest collection API replacement

- The control API mounts `/collections` for create/list/detail and operator pause/resume/cancel.
  Graph, crawl-plan, run, and coverage submission routes are no longer mounted. API startup does
  not provision graph delivery or run graph scheduling/outbox tasks. Crawler owns frontier execution.
  The obsolete coverage HTTP adapter and its tests are deleted; other graph internals and their
  unmounted adapters still require removal with their remaining callers.
- Materialization and catalogue status use process-owned API JetStream/catalogue-worker handles
  directly, with no graph runtime dependency. Collection status uses bounded grouped current-state
  reads under a short shared control-row lock, so accounting and interest counts agree. Reads do
  not execute work or provision delivery infrastructure.
- Public credentials can create/read only public collections within the public depth/page limits.
  Private intent, non-default priority/access context, and collection actions require admin access.
  Public list/detail filtering excludes private records. Query readiness remains explicitly unknown
  until catalogue evidence is verified. End-to-end private observation/content filtering remains
  required before deployed private acquisition is safe.
- Retained collections default to a 1,000-record ceiling; retained interests default to 200,000.
  Their admission checks serialize with creation/reservation. Identical retries remain usable at
  capacity. Cancellation retains deduplication identities; acknowledged-evidence cleanup must reclaim
  these record slots. Aggregate checkpoint/navigation byte limits and cleanup are still required.
- Collection POST bodies are capped at 512 KiB before JSON parsing; intent and query envelopes have
  their tighter existing bounds. Allowed sections reject credentials, query strings, fragments,
  invalid schemes, and oversized sections. Page responses separate acquisition, selection, useful
  supply, failure, sharing, reuse, reservation, and consumed page units.
- Five API tests cover durable/idempotent submission, private filtering, operator controls, capacity,
  malformed/big payloads, pagination bounds, and the actual application OpenAPI route set. The store
  test covers interest-capacity/dedup semantics. A new live Postgres race checks collection capacity.
- Frontends and SDK still call superseded routes and must be changed before cutover. Text descriptions are now a collection input. No compatibility route is retained and no services were restarted.
- Final `make check` passes: 338 backend tests (six opt-in Postgres skips), 11 SDK tests,
  package checks, and both frontend typechecks/builds. All six live Postgres tests pass separately.
  Frontend compilation does not prove compatibility with the new collection API; those callers
  still require replacement and end-to-end verification.

## Latest description discovery integration

- Collections accept `seed_description` alongside URLs and/or seed SQL. Discovery produces candidates
  for the same frozen seed checkpoint and normal admission path. The control API reports planning,
  searching, selecting, validating, or complete, plus retained search queries and resolved URLs.
- Crawler discovery runs one provider call or DNS validation per pass. Plans contain at most three
  queries; searches retain at most 20 bounded candidates each; the model selects at most ten existing
  candidate IDs. Each provider response is byte/time bounded. Search results and descriptions are
  untrusted data, and provider output cannot create URLs absent from those results.
- Checkpoint writes require the current unexpired collection claim and expected revision. Pause,
  cancellation, expiry, or a competing committed revision prevents stale writes. Each accepted phase
  survives restart. DNS rejects non-public addresses; transient DNS/provider failures defer discovery.
  An initial DNS check is still not a substitute for the CDP service's network egress boundary.
- The provider-returned model identity is frozen at planning and reused for source selection. Query
  lists/model identity join compact seed provenance in collection outcome evidence. A new runtime
  test exposed and fixed premature seed-checkpoint disposal: finishing admission now retains compact
  provenance while releasing candidate storage. This fixes SQL provenance retention too.
- The old coverage resolver, store, schemas/model, description-to-graph executor, and their obsolete
  execution tests are deleted. URL-section matching moved under collections. Old coverage schema
  migrations remain until the coordinated replacement migration/reset. No compatibility API is added.
- `PERIPLUS_DISCOVERY_MODEL` replaces the old coverage-model variable; discovery secrets move from API
  to crawler in Compose and Helm. The app has no implicit model. Helm lint/render and Compose config
  validation pass. Local dev services and data remain untouched.
- Four provider tests cover phase replay, model pinning, candidate identity, outages/byte limits, and
  DNS validation. Two store tests cover discovery fencing and normal admission after discovery. The
  assembled runtime test now covers description discovery through one capture and durable provenance.
  API tests verify description submission does not itself run discovery.
- The structured-output envelope was checked against [official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
  Provider behavior is tested with simulated HTTP; no real model or search call has been made.
- Final `make check` passes after resolver removal and the model-pinning assertion: 331 backend
  tests (six opt-in Postgres skips), 11 SDK tests, package checks, and both frontend typechecks/builds.
  All six live Postgres tests pass with discovery checkpoint fields present. Provider HTTP remains
  simulated in tests, and the replacement has not been deployed.

### Frozen evidence visibility and public content filtering

- Acquisition context and visit evidence now carry frozen public/private visibility. Both normal
  capture and uncertain terminal recovery preserve it. Outcome acceptance rejects evidence with
  another requested URL or visibility before publishing anything.
- Public observations and link occurrences filter against public visit evidence. Content objects
  require a matching public visit/document association; HTML elements require a public content
  object. The subtree helper inherits that restriction. Existential filters preserve content grain
  when several public/private observations have identical bytes.
- A real DuckLake catalogue test covers private-only content, a caller knowing its content identity,
  helper execution, and shared bytes becoming public without revealing private link provenance.
  Frontier tests cover mismatched visibility/URL rejection and private recovery context.
- This changes the destination physical visit schema directly; the coordinated schema version bump
  and disposable-state reset remain part of cutover. No services or local data were changed.
- Final verification: `make check` passes with 333 backend tests (six opt-in Postgres skips),
  11 SDK tests, and package/frontend checks. The normal acquisition boundary test explicitly uses
  private visibility and asserts returned visit evidence preserves it. `git diff --check` passes.

### Versioned operator controls

- Added administrative GET/PUT `/frontier/controls` with typed settings for global pause,
  retained collections/interests, pending admission, dispatch concurrency, and dispatch rate.
  A null rate explicitly removes the rate ceiling; zero is rejected. Pausing stops new dispatch
  and delayed capture starts while already started captures finish under frozen requirements.
- Settings replacement checks `expected_version` while holding the same short control-row lock as
  dispatch. Concurrent stale edits receive HTTP 409. Successful changes record time and authenticated
  service-role attribution; the infrastructure API has no end-user identity to attribute.
- Lower limits do not evict admitted work. Rate changes calculate eligibility from the last dispatch,
  so slowing the crawler does not create an immediate extra slot. Each dispatch generation records
  the effective control version. Counters describe pending/dispatched acquisitions, not successes or
  browser cost, and this endpoint is admin-only so aggregate private work is not leaked.
- Added administrative collection-priority updates over the existing fair scheduler. Settled
  requests reject priority/pause changes with HTTP 409. Frozen collection intent remains unchanged.
- API/store tests cover authorization, version conflicts, limit validation, rate eligibility,
  pause/resume, and priority changes. An isolated Postgres race checks that only one of two edits
  against the same version succeeds.
- Physical-attempt/time allowance accounting, per-domain dynamic eligibility, background allocation,
  durable policy-history evidence, operator UI, and cutover remain outstanding; this endpoint does
  not claim to enforce monetary or browser-time ceilings.
- Verification: `make check` passed (336 backend tests, six then-existing opt-in Postgres skips,
  11 SDK tests, and package/frontend checks). The subsequently added concurrency test and all six
  existing live Postgres tests passed together (seven total). `git diff --check` passed.

### Physical attempt and client-time allowances

- Global controls now include cumulative attempt and client capture-time allowances, plus a frozen
  per-attempt capture timeout. Defaults are 10,000 attempts, 86,400,000 ms aggregate client time,
  and a 120,000 ms capture timeout. A reservation adds a 5,000 ms cleanup allowance. Zero operating
  allowance stops dispatch. Operators increase total allowance to resume; counters never reset as
  a side effect of settings changes. Limits may be lowered below outstanding work, which finishes
  against its already frozen reservation while further dispatch stops.
- Dispatch reserves one physical attempt and its client-time allowance in the same transaction as
  request page consumption and outbox publication. Sharing does not multiply the reservation.
  Starting transfers the count from reserved to started; completion or retry reconciles measured
  monotonic capture time, including CDP setup and cleanup. Request page units remain consumed once.
- Expired unstarted delivery releases both physical reservations. A started attempt with no accepted
  outcome records uncertainty and charges the full reserved time. Repeated recovery/replay cannot
  charge twice. Known overruns are charged at the measured value, never clipped to the reservation.
  Store acceptance validates reservation identity and preserves all previously recorded attempts.
- The capture primitive receives the frozen timeout and bounds cleanup to five seconds. Captured
  evidence and retry evidence carry the policy version, reservation, and measured time; uncertain
  evidence leaves measured time null. These fields persist in `ingest.attempts.resource_usage` and
  survive exact evidence replay. Imports may omit resource usage rather than invent measurements.
- This is client-time accounting, not a guaranteed provider billing ceiling or proof that remote
  browser activity stopped. Standard CDP provides no provider cost bound. Control responses label
  that distinction and expose reserved/started attempts, reserved/charged client time, and specific
  pause, rate, capacity, or allowance waiting reasons. Background-specific allowance and provider
  outage circuit behavior still need implementation before background exploration is enabled.
- Tests cover sharing, allowance exhaustion/refill, cancellation/recovery, unknown usage, known
  overrun, immutable retry usage, real timeout during a hanging CDP connection, and DuckLake replay.
  Eight isolated live Postgres tests pass, including concurrent dispatch for one remaining attempt.
  Development data and running services remain untouched; physical schema cutover is still pending.
- Final `make check` passed: 345 backend tests (eight opt-in Postgres skips), 11 SDK tests,
  package checks, and frontend checks/builds. The eight live Postgres tests also passed separately.
  `git diff --check` passed. This is not a full FRONTIER acceptance or deployed cutover result.

### Durable evidence and lineage receipt reconciliation

- The frontier now retains a committed DuckLake snapshot and acknowledgement time on each evidence
  outbox record, independently of its queue-publication marker. Observation receipts also populate
  the acquisition's evidence snapshot. Successful publication alone leaves these fields unset.
- A bounded crawler loop claims at most eight due receipts with expiring tokens and Postgres
  `SKIP LOCKED`, uses process-owned ingestion handles with five-second I/O limits, and backs off
  pending/failed/unavailable checks to at most five-minute intervals. It provisions no infrastructure
  and holds no Postgres lock during NATS or DuckLake work. Due times prevent fresh work from always
  overtaking already deferred receipts.
- Receipt validation compares the complete immutable job (ignoring enqueue time) and its successful
  result against the current claimed outbox row. Stale claims, conflicting evidence, pending state,
  and terminal failure cannot advance the evidence watermark. The first accepted snapshot remains
  stable across replay.
- This reuses the existing ingestion result KV and durable ingestion lane. Missing/expired receipts
  replay the same immutable job for ingestor lake reconciliation; pending jobs may be republished
  with the same delivery identity. This never dispatches a new acquisition. Definitively failed jobs
  retain their failure for existing operator recovery rather than automatically retrying bad evidence.
- Collection status now reports ingested pages and separately confirmed terminal lineage. It does
  not infer materialization/query readiness from an ingestion receipt. These fields include only
  the collection's own visible associations; global/private counts are not added to public responses.
- Store, relay, and queue tests cover pending/failed receipts, loss and replay, exact evidence matching,
  stale tokens, snapshot persistence, and independent query readiness. Nine isolated live Postgres
  tests pass, including disjoint receipt claims by concurrent workers.
- Safe cleanup still requires the outstanding historical-check watermark and retained-navigation
  pin rules; this change does not delete operational data or enable background allocation. Lake
  lookup benchmarking, background admission, cleanup, full materialization readiness, UI replacement,
  and coordinated cutover remain outstanding.
- Final `make check` passed: 351 backend tests (nine opt-in Postgres skips), 11 SDK tests,
  and package/frontend checks and builds. All nine live Postgres tests passed separately.
  `git diff --check` passed. No deployment or development-state reset occurred.

### Bounded historical seen checks and cleanup watermark foundation

- Added a typed 64-URL / 64-KiB historical query contract over public terminal observations.
  Successful effective URLs also count; private evidence and failed effective URLs do not. The
  existing query client supplies the complete selected set, query identity, and consistent snapshot.
  Any malformed, truncated, unavailable, or unlabelled result remains a dependency failure.
- Each check registers against retained public navigation before the lake query. One active check
  per parent and a total cap of 128 bound stored candidates. Tokens fence retries and two-minute
  expiry forces a fresh query. Expired rows are reclaimed under the frontier control lock. Database
  time is read after acquiring that lock, avoiding stale transaction-start time or worker clock skew.
- The cleanup predicate blocks markers newer than any outstanding check snapshot and blocks all
  marker cleanup while an unexpired check has no snapshot. Tests cover the ingestion-gap boundary,
  expiry, replacement tokens, disabled exploration, and private navigation. Actual marker deletion
  and atomic background admission still need integration; no background scheduling is enabled here.
- A synthetic local DuckLake benchmark exercises one and ten million observations. Plan evidence
  led to a semantics-preserving internal query change that applies candidate predicates before the
  document join. Differential rows/types/order were checked at the same snapshot. Results and
  limits are recorded in QUERY.md; the reproducible script retains no development data.
- Ten isolated live Postgres tests pass, including concurrent checks for one parent. Full background
  extraction/admission, domain/trap rules, allocation, retention pins, cleanup, and UI remain pending.

- Final `make check` passed: 357 backend tests (ten opt-in Postgres skips), 11 SDK tests,
  and package/frontend checks and builds. All ten live Postgres tests passed separately.
  The ten-million-row same-snapshot differential benchmark also passed (original 4,198 ms warm,
  revised 935–949 ms warm). `git diff --check` passed. No deployment or development reset occurred.

### Atomic background admission and durable selection provenance

- Background candidates now pass a final control-locked admission transaction after the historical
  query. It validates the stored check token, expiry, current policy version, frozen candidates,
  and accepted query result. Caller-supplied changes cannot turn a seen URL into an unseen URL.
- Current public requested URLs and successful effective URLs close the capture-to-ingestion gap.
  Concurrent parents admitting one URL produce one acquisition and one already-pending decision.
  The active background URL key is unique and clears when the acquisition becomes terminal.
- Conservative background-only traps reject credentials, local/non-global literal addresses,
  calendar/session paths, repeated/deep paths, excessive or duplicate query parameters, and bounded
  pagination/facet patterns. These checks preserve URL identity. DNS and CDP egress enforcement
  remain separate outstanding work; these lexical checks do not establish network safety.
- Admission bounds global pending work, pending background work, and per-domain pending work.
  Capacity deferral retains an undecided candidate; it can resume when capacity returns. Complete
  batches mark their parent selected, while short-lived checks retain their cleanup watermark.
- Background work owns no synthetic collection or interest and consumes no request page budget.
  Compatible request work may join its queued acquisition; cancelling that request preserves the
  independently needed background work. Zero allocation preserves pending background work.
- Frozen background reasons now retain their historical query identity, source snapshot, and policy
  version in typed lineage evidence and the public acquisition-reason relation. DuckLake replay
  tests verify exact replay and reject conflicting provenance.
- Full `make check` passed: 365 backend tests (11 opt-in Postgres skips), 11 SDK tests, and
  package/frontend checks and builds. All 11 isolated live Postgres tests passed separately,
  including the concurrent background admission race. The latest targeted suite passed 61 tests.
- Autonomous background selection and scheduling are still disabled: the scheduler currently selects
  request-associated acquisitions only. Explicit allocation, separate background physical allowances,
  navigation continuation, retention cleanup, UI/SDK replacement, and coordinated cutover remain.
  No running service or development data was changed, and this is not full FRONTIER acceptance.

### Background scheduling and physical allowances

- The scheduler now selects independent background acquisitions alongside request work. Under
  contention, a durable fractional dispatch credit applies the configured background percentage;
  request ranking still uses collection priority and scheduling age. Shared acquisitions appear
  only in the request lane. Either lane can use spare capacity without accumulating future debt.
- The admin controls expose `background_share` (0–99 percent, default zero). The upper bound
  preserves a request allocation even when an operator strongly favors exploration. Share describes
  dispatches, not measured browser-time throughput. Actual rate and active-domain limits still apply.
- Background-only attempts reserve from separate cumulative attempt and client-time allowances as
  well as global allowances. Defaults are 1,000 attempts and 12,500,000 milliseconds; operators can
  set either to zero. Status exposes reserved/started attempts, reserved/charged time, and a distinct
  background waiting reason. Exhausted background allowance does not block request-associated work.
- Cancellation after shared dispatch cannot bypass background limits: if its last live request
  disappears before start, continuing for the background reason must first reserve background
  allowance. The reservation commits before CDP acquisition. Retries reserve again; duplicate starts
  are fenced. Unstarted recovery releases both reservations, measured completion reconciles both,
  and uncertain started work charges the frozen bound to both.
- Targeted store tests verify an exact 25-percent allocation while both lanes stay eligible, spare
  capacity, shared work at zero background allowance, cancellation before start, and uncertain
  recovery. Twelve isolated live Postgres tests pass, including concurrent scheduler calls competing
  for one remaining background attempt.
- Autonomous candidate extraction is not yet connected to the runtime. Navigation beyond request
  depth, global exclusions, retained-state cleanup, frontend/SDK replacement, migrations/reset, and
  full live acceptance remain outstanding. Source changes have not been deployed.
- Final `make check` passed: 371 backend tests (12 opt-in Postgres skips), 11 SDK tests, and
  all package/frontend checks and builds. All 12 live Postgres tests passed separately.
  `git diff --check` passed. Development data and running services remain untouched.

### Autonomous background selection loop

- The crawler now runs background selection beside collection selection, dispatch, receipt recovery,
  and capture lanes. Completed public navigation is claimed independently of collection settlement.
  Claims expire after two minutes, are capped at 128, and use database time after the control lock.
  Private acquisitions and absent navigation cannot be claimed.
- The initial deterministic policy considers the first 1,000 distinct ordered navigation URLs,
  applies background traps, retains at most four URLs per host, and round-robins hosts into a batch
  of at most 64 URLs / 64 KiB. These are explicit exploration limits, not a complete-page coverage
  promise. Navigation execution remains bounded by the existing standalone SQL reader.
- A registered historical check and accepted snapshot survive capacity deferral. Resumption reuses
  the frozen candidate set and query result; expiry or policy change requires a new query. Partial
  admission is idempotent. Lookup failure admits no new work and applies bounded retry backoff;
  empty/trap-only navigation settles without a historical query.
- Public leaf captures retain navigation while background share is enabled, allowing exploration
  beyond their request boundary. Private navigation follows only its own request traversal needs.
  No background interest or synthetic collection is created and originating requests do not wait
  for background selection. Zero share disables new service claims and background dispatch.
- Cancellation drains the bounded selection thread before releasing ownership. Stale service
  tokens cannot release or complete replacement claims. Parent state retains its next service time,
  failure count, and dependency error for subsequent progress reporting and retention decisions.
- Thirteen isolated live Postgres tests pass, including concurrent service claims selecting the
  sole public parent. Local tests cover bounded host diversity, private/absent navigation, capacity
  resumption without another query, lookup failure, empty pages, stale ownership, policy change,
  and public leaf navigation enablement.
- Global exclusions/egress enforcement, safe retained-state cleanup, public progress/UI/SDK work,
  replacement migrations, coordinated reset, and full live acceptance remain outstanding. This
  source integration has not been deployed or enabled against running development services.
- Final `make check` passed: 378 backend tests (13 opt-in Postgres skips), 11 SDK tests,
  and all package/frontend checks and builds. All 13 live Postgres tests passed separately.
  `git diff --check` passed. No development-state reset or service restart occurred.

### Current global URL exclusions

- Admin frontier settings now accept up to 100 typed exclusion rules: a hostname, `*.hostname`
  (the named host and its subdomains), or `*`, plus an absolute path prefix. Rules cover both HTTP
  and HTTPS, compare decoded path-segment boundaries, and ignore query/fragment when matching.
  They do not alter URL identity. Hostnames normalize to ASCII/IDNA; ambiguous pattern syntax,
  embedded credentials/ports, and query-bearing rule paths are rejected.
- Request admission, recent-result reuse, and background admission consult current rules under
  the control lock. Reuse also checks the recorded effective URL. Collection selection advances
  past excluded candidates without reserving pages; background checks record a declined decision.
- Dispatch and physical start recheck exclusions. Queued exclusion releases request reservations;
  exclusion after dispatch preserves consumed request pages but releases an unstarted physical
  reservation. Acquisition status records `global_exclusion`. Started captures finish; a later
  retry can be excluded while retaining all preceding attempt evidence.
- Recovery includes a 64-acquisition wraparound scan, independent of pause and exhausted physical
  allowance. A durable cursor prevents the same first batch from hiding later queued/delayed work.
  This bounds propagation work per pass while the pre-start check remains immediate for deliveries.
- Fourteen isolated live Postgres tests pass, including the exclusion-update/start race. Store and
  rule tests cover no-charge admission rejection, paused reconciliation, delivery fencing, uncertain
  attempt retention, background decline, decoded segment boundaries, and excluded effective reuse.
- These checks govern admitted URLs and recorded reuse destinations. Enforcement inside an active
  capture (redirect/subresource requests), DNS/egress policy, dynamic domain controls, retention,
  UI/SDK replacement, and live cutover remain outstanding. No service has been restarted or reset.
- Final `make check` passed: 386 backend tests (14 opt-in Postgres skips), 11 SDK tests, and
  all package/frontend checks and builds. All 14 live Postgres tests passed separately. The store
  suite also passed 63 tests after adding the effective-URL reuse regression during the full check.
  `git diff --check` passed. Full FRONTIER acceptance remains unproven and the goal stays active.

### Page-session exclusion interception

- Starting a physical attempt now freezes the current exclusion rules and their control version
  on the acquisition. The worker reloads that authorized attempt before remote I/O, so a policy
  edit between dispatch and start is respected. Already-started attempts retain their snapshot.
- The existing document-response CDP handler also handles request-stage interception when rules
  exist. Matching redirect hops and subresources receive `Fetch.failRequest`; allowed requests
  continue. Request and response stages share one CDP session. Non-HTML body capture remains intact.
- The main frame ID distinguishes an excluded main navigation from a blocked child frame. Main
  navigation exclusion produces a terminal `global_exclusion` failure rather than a retryable
  navigation error. Required interception setup occurs before navigation without an unchecked
  fallback. The protocol behavior follows the official
  [CDP Fetch contract](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/).
- Four interception unit tests pass. An opt-in isolated installed-Chromium test against a local
  HTTP server verifies that the allowed page loads and excluded redirect/script requests never
  reach the server. The test creates a disposable browser session; it does not restart deployment
  services or alter local crawl data. All 14 isolated live Postgres tests also pass.
- This is page-session enforcement, not a complete browser network firewall. Worker/other-target
  traffic, DNS/egress validation, dynamic domain policy, retention, frontend/SDK integration, and
  coordinated live cutover remain outstanding. Full FRONTIER acceptance is not yet established.
- Final `make check` passed: 393 backend tests (14 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. The 14 live Postgres tests and five
  interception tests with real Chromium enabled passed separately. `git diff --check` passed.

### Bounded request identity and selection checkpoint corrections

- A live Postgres regression reproduced a 6,464-byte index entry exceeding the 2,704-byte limit
  on the former `(collection_id, url)` unique index. Accepted long URL input must not fail merely
  because the physical deduplication index stores the entire string.
- Request interests now index `(collection_id, url_key)`, where `url_key` is the SHA-256 of the
  complete normalized URL. The original URL remains authoritative and is compared after lookup;
  a digest collision fails without sharing two different URLs. Query parameters remain opaque,
  fragments normalize away, and the first committed context still wins. This directly replaces
  the physical key; no compatibility reads, aliases, or migration bridge were added.
- Fifteen live Postgres tests pass, including concurrent admission of the same long URL and a
  distinct URL differing only in its query suffix. Selection checkpoint lookups use the same key
  plus full-string equality. A simulated collision test verifies transaction rollback.
- Fixed exclusion selection: advancing past a declined candidate now verifies the current
  exclusion in the same transaction. If the rule changed, selection keeps its cursor and retries
  under the new policy. Unaccounted, non-excluded work still cannot advance. Tests exercise the
  complete seed-selection pass without creating fake interests for excluded URLs.
- A recent result with an excluded effective URL is unavailable for reuse; its still-eligible
  requested URL may instead receive a fresh guarded acquisition. This avoids an endless selection
  wait caused by treating a historical redirect as an exclusion of the requested URL itself.
- Branch reuse now tests for an actual navigation reference, including after eviction stores JSON
  null. Leaf reuse can still use retained evidence. The eviction regression is a prerequisite for
  safe storage-bounded retention; the cleanup implementation and full live cutover remain pending.
- Final `make check` passed: 398 backend tests (15 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 15 live Postgres tests passed
  separately after replacing the failing long-URL index. `git diff --check` passed.

### Frontier-owned navigation retention

- Replaced the janitor's graph-run ownership lookup and old graph-run deletion with direct frontier
  navigation ownership checks. The janitor validates the replacement schema and uses Postgres plus
  the existing object repository; it no longer provisions graph delivery storage or connects to NATS.
- A short control-locked transaction checks terminal state, age, acknowledged observation and
  acquisition-lineage receipts, active interests, background allocation, service claims, and active
  historical checks before revoking a navigation reference. Remote deletion happens after commit.
  Request/background admission cannot pin a reference concurrently after revocation.
- Retirement preserves the compact original reference for exact completion replay, while branch
  reuse requires the live reference. Evidence and historical seen markers are unchanged. Failed
  deletion leaves an unreferenced object that a later scan can delete idempotently. Orphan objects
  without an acquisition wait two hours and only strict acquisition/hash navigation keys qualify.
  Raw HTML is never a cleanup target.
- The process retains a streaming listing iterator between 500-object metadata batches, so pinned
  or young objects at the beginning do not hide later candidates. Disk listing no longer sorts the
  complete subtree into memory. Scan errors restart listing; shutdown drains any active deletion
  thread. The existing navigation grace and janitor interval settings still apply.
- Sixteen isolated live Postgres tests pass, including the retirement/reuse race: either the branch
  pins the retained package or it receives a fresh acquisition after retirement. Store tests cover
  delayed receipts, selecting interests, background/check pins, exact completion replay, leaf versus
  branch reuse, strict key ownership, and live/recent objects. Janitor tests cover scan continuation
  and failed-delete retry.
- This retires navigation objects only. Bounded operational record/outbox cleanup, aggregate retained
  state ceilings, historical API reads after record cleanup, dynamic domain/egress enforcement,
  frontend/SDK replacement, and full cutover remain outstanding. Running services and development
  data have not been changed.
- Final `make check` passed: 406 backend tests (16 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 16 live Postgres tests passed
  separately. `git diff --check` passed. No service restart, object deletion, or data reset was
  performed against the running development deployment.

### Aggregate retained acquisition ceiling

- Added the admin `acquisition_limit` control (default 10,000, maximum 1,000,000) independently of
  pending admission and active dispatch limits. It counts all retained acquisitions, including
  terminal results and unacknowledged evidence, so repeatedly increasing physical attempt allowance
  cannot silently permit unlimited operational acquisition records.
- New request acquisitions, independent background acquisitions, and paused-interest splitting
  check this ceiling under the existing control lock. Compatible sharing and successful recent
  reuse create no acquisition and remain possible at capacity. Existing queued work can dispatch
  unless it requires a new record for a paused participant; that split waits without consuming
  request pages or a physical attempt reservation.
- Status reports the actual retained count and a separate `retained_acquisition_capacity` admission
  waiting reason. Lowering the limit preserves existing records; increasing it permits pending
  admission to resume. Counts read authoritative state rather than maintaining another cleanup
  counter that could drift.
- Seventeen isolated live Postgres tests pass, including two independent requests racing for the
  last retained-acquisition slot. Store tests cover terminal retention, sharing/reuse at capacity,
  paused splitting without charges, and a background checkpoint resuming after capacity increases.
- Record/outbox reclamation and historical API reads still need implementation. This ceiling makes
  storage pressure explicit while that work proceeds; it is not a substitute for continuous cleanup
  or full FRONTIER acceptance. No deployment state has been reset or restarted.
- Final `make check` passed: 410 backend tests (17 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 17 live Postgres tests passed
  separately. `git diff --check` passed.

### Unreferenced acquisition and outbox reclamation

- The janitor now also reclaims terminal acquisitions older than one hour when they have no
  request interests and no live navigation reference. A durable wraparound cursor bounds each
  pass to 64 candidate records and lets later candidates progress past earlier pinned records.
- Deletion requires the observation receipt, every associated observation/lineage outbox receipt,
  expired service/check ownership, and the historical-check watermark. Public markers newer than
  an outstanding check snapshot remain; a check without a snapshot blocks public evidence cleanup.
  Cancelled work that never started may be reclaimed without inventing an observation.
- Associated outbox and expired parent-check rows are deleted in the same short control-locked
  transaction as the acquisition. Physical usage counters remain cumulative. No raw object or
  durable lake evidence is deleted, and freed rows restore retained-acquisition capacity.
- Acquisitions referenced by any request interest remain, even when that interest has settled.
  Collection/interest reclamation waits for the historical API handoff rather than making existing
  collection status or request identity disappear prematurely.
- Eighteen isolated live Postgres tests pass, including competing cleanup transactions deleting
  one record exactly once. Store tests cover missing receipts, old and unknown check snapshots,
  expired checks, a seen decision surviving marker deletion, reference pins, and cursor progress.
  A janitor loop test verifies that frontier cleanup is actually invoked; shutdown drains its
  transaction thread before returning.
- Historical collection reads, collection/interest reclamation, dynamic domain and broader egress
  enforcement, UI/SDK replacement, migration/reset, and complete live acceptance remain pending.
  No running deployment records, objects, or services were modified by these source/test changes.
- Final `make check` passed: 416 backend tests (18 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 18 live Postgres tests passed
  separately. `git diff --check` passed. The full goal remains active.

### Historical collection detail and bounded request retirement

- Added fixed, parameterized historical collection reads through the API's existing serialized
  catalogue owner. No new service, credential, or connection is introduced. History permits one
  outstanding read, uses a ten-second SQL interruption deadline and two-row limit, caps JSON values,
  drains cancelled work, and treats query/storage errors as dependency failures.
- Detail responses distinguish current state from immutable history. Historical counters remain
  unknown when no outcome has arrived, and materialization readiness is not inferred. Public reads
  filter authoritative lineage visibility; inconsistent embedded visibility fails closed. The
  existing current-state list explicitly labels its scope.
- Caller-supplied IDs absent from current state consult immutable history before creation. Identical
  intent returns the historical request, different intent is rejected, and a private historical ID
  cannot be recreated by a public caller. History outages return retryable unavailability. Fresh
  server-generated IDs and already-current requests do not require a historical lookup.
- Collection cleanup now requires committed definition and outcome receipts, every associated
  lineage receipt, and accepted evidence receipts. It marks the collection retiring before pruning
  at most 512 dependent interest/outbox rows per pass. API reads use immutable history during this
  phase, so partial pruning never makes visible request counts shrink. The final row is removed
  only when its dependents are gone; both collection and interest capacity become reusable.
- The janitor invokes collection cleanup before acquisition cleanup. A retirement cursor prevents
  large requests or pinned candidates from monopolizing a pass. Active selection ownership and
  unsettled interests remain protected. Replay racing row removal uses history without recreating
  control state or acquisition work.
- Real DuckLake tests cover partial arrival, terminal counters, missing IDs, and private visibility.
  API tests cover detail, immutable replay, conflicts, outages, private IDs, partial pruning, and
  row-removal races. Store tests cover receipt gates, full dependency release, missing receipts,
  and bounded pruning of a 600-interest request. All 19 live Postgres tests pass, including
  concurrent collection retirement deleting one request exactly once.
- Historical browsing in the final frontend/SDK, complete query readiness, dynamic domain/broader
  egress enforcement, old graph removal, replacement migrations, reset, and full live acceptance
  remain pending. No running deployment data or service was changed.
- Final `make check` passed: 429 backend tests (19 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 19 live Postgres tests passed
  separately, and the history tests exercised real DuckLake. `git diff --check` passed.

### Paginated historical collection browsing

- Added `GET /collections/history` before the collection identity route, with an explicit public
  read capability and administrator access to private history. Bounded summaries preserve unknown
  outcome counters, carry source/as-of information, and link by immutable collection identity.
- Keyset pagination uses definition timestamp and UUID, with a maximum 100 rows plus one lookahead.
  Visibility filters run before pagination. Bounded JSON extraction and consistency checks prevent
  oversized or contradictory intent from exposing summaries; duplicate page identities fail closed.
- List/detail share the existing catalogue read slot and deadline. Cursor validation occurs before
  catalogue work. Storage failures and privacy inconsistencies return retryable 503, never empty
  history. Pagination is explicitly a live feed; late arrivals above a cursor require refresh.
- Targeted checks passed: 18 collection API/history tests and nine real DuckLake lineage/history
  tests. These cover timestamp ties, cursor continuation, public filtering before limits, private
  admin reads, partial arrivals, oversized/inconsistent intent, shared capacity, route precedence,
  and unavailable storage. The first API test exposed the missing public middleware capability;
  it was corrected before the passing run.
- Dynamic domain controls are still pending: dispatch and capture currently read frozen domain
  limits, and domain policies have no pause field. Replacement migrations, frontend/SDK integration,
  full query readiness, old graph removal, coordinated reset, and live acceptance remain open.
- Full `make check` passed: 433 backend tests (the 20 existing opt-in integration skips),
  11 SDK tests, and package/frontend checks and builds. Afterwards the oversized-intent assertion
  was isolated into its own test so a visibility error could not mask it: all ten real DuckLake
  lineage/history tests passed. Four access-boundary tests also passed with explicit history GET
  permission and denial of history writes/adjacent paths. `git diff --check` passed.
- Next domain-control work must also move pacing eligibility ahead of physical start authorization:
  `acquire_page` currently waits for the domain interval after `begin_attempt`, so a long domain wait
  occupies dispatched capacity and an authorized physical attempt before CDP starts. Current domain
  permit state already owns the concurrency and pacing coordination; this needs to be integrated
  with frontier eligibility, not replaced by a second pacing authority.

### Domain waiting before physical attempts

- Frontier capture now checks the existing NATS domain pacing state without waiting. An immediately
  eligible worker reserves a start atomically; a deferred worker does not advance the pacing cursor.
  Concurrent pending checks therefore cannot reserve an arbitrarily long sequence of future starts.
  Shared 429/5xx backoff still applies when the configured interval is zero. CAS contention is bounded.
- A fenced `defer_unstarted` transition releases the physical attempt/time reservation and active
  delivery slot, returns the acquisition to pending retry eligibility, and advances its generation.
  Request participants and consumed page units stay frozen. Deferral does not increment physical
  attempts, charge browser time, or allow old deliveries to start. Other pending URLs on that domain
  receive the same eligibility hint, allowing other domains through the scheduler's candidate window.
- Capture acquires its domain permit, checks immediate pacing, and only then calls `begin_attempt`.
  Busy permits also defer without a physical start. The acquisition service receives the held permit
  and already-reserved start, so it does not perform a second pacing wait after authorization.
  A policy/cancellation race after pacing reservation can conservatively waste one interval, but
  cannot authorize remote work through a rejected start.
- Targeted checks passed: 83 frontier-store tests, nine domain-policy/pacing tests, four capture tests,
  and two assembled-runtime tests. All 20 isolated live Postgres tests passed, including repeated
  deferral/start races with exactly one accounting winner. Acquisition-boundary coverage verifies
  an already-reserved start skips the second wait and requires a held permit.
- This completes the nonblocking pacing prerequisite, not live domain policy edits: dispatch/start
  still need current domain policy resolution, pause/version attribution, and corresponding controls.
  No new schema, service, queue, deployment reset, or live capture was introduced by this increment.
- Final `make check` passed: 439 backend tests (20 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. All 20 Postgres tests passed separately
  against isolated schemas. `git diff --check` passed. Full live frontier acceptance remains pending.

### Current domain policies at dispatch and physical start

- Domain policies now carry independent pause state, a positive version, and an updating actor.
  Administrative PATCH and DELETE require the expected version and reject stale edits with 409.
  Policy mutation and frontier start/dispatch serialize on the existing short control transaction;
  no database lock spans NATS or browser work. Disabling a rule exposes the next matching rule;
  pausing keeps the most-specific rule active and blocks its domain. Global pause remains separate.
- Both direct and fair dispatch resolve current domain concurrency/pause. Correlated policy matching
  excludes paused or saturated domains before the bounded candidate window, preserving progress on
  other domains. Exact matches outrank wildcard matches, then the enabled catch-all rule.
- Delivery resolves current policy before obtaining a domain permit and pacing reservation, then
  passes that snapshot to final start. A changed snapshot or pause rejects authorization. A started
  attempt freezes its domain snapshot and retains it through subsequent policy edits and recovery.
- Pacing stores the last reserved start rather than accumulating future reservations. The current
  interval is applied to that timestamp, so interval increases/decreases affect the next eligibility
  check. Removed the superseded waiting reservation function and acquisition-service fallback:
  frontier is the sole runtime caller and supplies its already-held permit and reserved start.
- Domain scheduling hints now carry policy identity/version separately from retry eligibility.
  A policy edit invalidates an old domain wait immediately, including faster pacing or resume,
  while preserving an independent retry backoff. NATS remains the pacing authority; hints never
  grant permission to bypass the final NATS/domain-policy checks.
- Attempt usage now persists the effective domain snapshot and exclusion-policy version in accepted
  and uncertain evidence. Acceptance validates them against authorized state. A real DuckLake
  round-trip found nested UUIDs reaching the JSON writer in Python form; `_attempt_values` now
  serializes that JSON field in JSON mode while retaining native types for physical columns.
- Targeted coverage includes current pause before an 80-item candidate window, queued concurrency
  changes, wildcard/exact precedence, disabled-rule fallback, stale final-start snapshots, frozen
  in-flight settings, domain-wait invalidation without erased retry backoff, and API version conflicts.
  All 21 isolated live Postgres tests passed, including an edit/start race and actual scheduler
  policy matching. All ten real DuckLake lineage/history tests passed after the serialization fix.
- Operational models and the NATS pacing shape have changed, but no running service/state was
  changed. Their replacement Alembic baseline and coordinated delivery/control/lake reset remain
  part of the pending cutover. Frontend domain controls, full visibility/readiness, dependency/egress
  checks, old graph removal, and complete live acceptance are still outstanding.
- Final `make check` passed: 445 backend tests (21 Postgres and one Chromium opt-in skips),
  11 SDK tests, and all package/frontend checks and builds. The 21 Postgres tests passed separately
  against isolated schemas. `git diff --check` passed. Builds do not yet verify the pending frontend
  contract replacement or coordinated end-to-end cutover.

### Standalone SDK replacement

- Removed the SDK's crawl/graph-run resource, graph status types, old lifecycle errors, and no-op
  close entrypoint. New typed collection intent and current/history snapshots use only collection
  endpoints. Submission accepts URL, description, or SQL intent through the same server contract;
  listing/history use bounded server pagination rather than filtering an unbounded client list.
- Collection handles expose refresh, local settlement waits, explicit administrative pause/resume/
  cancel/priority, and unsuccessful/partial outcomes. History stays distinguishable from current
  execution and rejects local controls. Partial history counters and query readiness remain unknown;
  settlement never claims ingestion/materialization completion.
- Wait deadlines cover sleeps and in-flight reads. Local cancellation never sends a remote cancel.
  Temporary 429/503 reads honor bounded Retry-After delays; mutations are sent once and preserve
  conflicts. Explicit submission IDs are preserved for server-side immutable replay checks.
- Added versioned frontier settings and domain policy clients, including pause, pacing, concurrency,
  expected-version updates/deletes, and typed status/allowance responses. Control behavior remains
  server-owned. Pydantic is an explicit standalone SDK dependency; backend models are not imported.
- Direct lake connection validation now expects the seven-view version-2 public relation set.
  Replaced the SDK README and installed-wheel smoke example with collection/fulfillment semantics.
  The smoke example requests one fresh depth-zero page, separately polls for public observation and
  fulfillment plus applicable HTML materialization, and bounds/drains its lake reads. It has not run
  against the live deployment yet.
- Four backend contract tests execute the SDK's actual HTTP transport against the real ASGI routes
  and frontier repository, covering normalized submission, controls, pagination, historical replay,
  API authorization, stale versions, and matching public catalogue registry. All four passed.
- All 16 SDK tests passed both in the backend test environment and the SDK's own environment. A
  newly built wheel was installed into an isolated Python 3.11 environment; imports proved no
  backend package was installed, the wheel contained no removed crawl module, and all 16 SDK tests
  passed there. The temporary wheel-test environment was removed. No package was published.
- Frontend/shell replacement, live status and complete readiness, dependency/egress gates, old graph
  deletion, replacement migrations, coordinated reset, and full live acceptance remain pending.
- Final `make check` passed: 449 backend tests (21 Postgres and one Chromium opt-in skips),
  16 SDK tests, and all package/frontend checks and builds. The smoke script compiles, and
  `git diff --check` passed. Live smoke execution awaits the coordinated replacement deployment.

### Operator crawler and collection views

- Replaced admin graph/run/schedule screens and their hooks/types with the shared crawler controls
  route; removed the unused graph-canvas dependency. Domain and content policy screens remain
  operator workflows, while the removed graph URLs now return the application's not-found page.
- Added typed, bounded polling for current frontier settings and explicit versioned edits. The form
  exposes dispatch pace, concurrent capacity, cumulative physical attempt/time allowances,
  background allocation, retained-state bounds, and host/path exclusions. Zero background allowance
  stays zero; unlimited dispatch pace uses the explicit null contract. Form drafts preserve their
  starting version and exact unedited millisecond values; conflicts require an explicit reload.
- Updated domain policy editing for current pause, version, and actor semantics, including stale-draft
  conflicts. Reads and mutations have timeouts. Failed refreshes retain visible data with a stale
  warning and disable controls until the next successful read. No optimistic mutation fabricates a
  server transition.
- Added admin collection list/detail routes with bounded current-state pagination and opaque-cursor
  history pagination. Operators can pause/resume, explicitly confirm cancellation, and adjust
  priority. Historical records expose no execution controls. Frozen intent, discovery provenance,
  partial seed admission, page accounting, supplied/failed results, shared/reused associations,
  ingestion and lineage receipts are visible. Unknown historical counters remain unknown, and
  unverified materialization readiness is never presented as complete.
- Admin typecheck, lint, all seven Node tests, and build passed. Public typecheck and build passed.
  An isolated Chrome browser check exercised real frontier store operations behind intercepted API
  requests: global pause, zero background, time conversion, stale policy conflicts, unavailable
  control/collection reads, domain pause/concurrency, request pause/resume/priority/cancel, historical
  unknowns, removed routes, and mobile overflow. Desktop/mobile screenshots were inspected. The
  conflict fixture was corrected to perform an actual concurrent change because identical control
  replacements intentionally do not increment the policy version. The owned preview server was
  stopped. This verifies browser behavior with isolated local state, not deployed integration.
- Collection creation forms, frontier-item/arrival drilldowns, full live velocity/eligibility views,
  verified materialization readiness, and the public/shell replacement remain incomplete. Dependency
  and egress gates, removal of old backend graph machinery, replacement migrations, coordinated
  disposable-state reset, and full live acceptance are still pending. No running service was changed.

### Operator collection submission

- Added `/collections/new` with URL, description, seed SQL/parameters, follow-link SQL, depth/page
  limits, recent-result age, section restrictions, local-time deadline conversion, visibility, and
  access context. It submits the existing collection contract without a graph wrapper. Example SQL
  uses the actual public observation columns. Server validation remains authoritative.
- Submission assigns an explicit UUID and freezes intent on the first send. No mutation automatically
  retries. An unconfirmed response exposes that identity and an explicit same-request retry; it does
  not silently allocate a new ID or allow changed intent under the old one. Successful responses
  open the collection detail route.
- Three new form tests cover conservative URL parameters, zero depth/fresh-result age, SQL bindings,
  private visibility, timezone conversion, and invalid/missing bounds or intent. Admin typecheck,
  lint, all ten tests, and build passed; public typecheck/build and diff whitespace checks passed.
- The isolated Chrome exercise additionally accepted a private SQL/URL collection in the real store,
  returned a simulated 503 after acceptance, verified that inputs froze, then retried and proved the
  exact same payload/identity was sent twice. The resulting detail opened successfully. The private
  checkbox's actual submitted value and zero reuse age were checked. Mobile screenshot inspected;
  owned preview stopped. No real discovery, CDP acquisition, or deployed service was invoked.
- Public submission replacement, live/item/arrival views, readiness proofs, remaining runtime gates,
  old backend removal, migrations/reset, and deployed end-to-end acceptance remain outstanding.

### Public collection contract replacement

- Removed public coverage-request API routes, proxy, types, and graph-progress components. The
  suggest page now submits collections and reads current or historical collection details through
  bounded no-store proxies authenticated only with the public API token. History has a cursor-paged
  public view; neither route nor browser exposes administrative controls.
- The simple form maps URL/description, depth, page budget, sections, and link-scope choices into
  CollectionSpec. Internal links mean the acquired page's site, including subdomains, represented by
  self/same_origin/same_host/same_site navigation values; external uses the external value. These
  are standalone follow-link SQL presets, not another traversal engine. All three generated SQL
  strings were executed through the actual bounded DuckDB selector with representative navigation
  rows. Public choices expose only supported depth/page limits.
- Explicit UUID submission freezes intent before sending; no automatic POST retry. An unconfirmed
  response retains its request link and offers a same-identity retry. Current views expose partial
  seed admission, waiting, supplied/failed pages, budget units, shared/reused associations, ingestion,
  lineage, and separately unverified query readiness. Historical missing counts stay unknown.
  Observation query links join web.fulfillment to web.observation and validate UUIDs before SQL
  interpolation; the old crawl_id predicate is gone.
- Public typecheck, lint, all 18 tests, and production build passed. Admin typecheck/build passed.
  Stale generated Next dev route types were moved out of the ignored cache after confirming no
  Next server was running; no source compatibility routes were restored. Diff whitespace passes.
- Isolated Chrome checks used real store-backed collection responses behind browser interception:
  acceptance followed by simulated 503, exact same retry payload, frozen controls, public visibility,
  detail polling and stale recovery, historical unknown counters, fulfillment SQL links, bounded
  history/refresh, and mobile layout. Screenshot inspected; owned local Next test server stopped.
  These checks do not prove deployment authentication, provider discovery, capture, or live ingestion.
- Crawler-wide Live status/velocity, frontier/arrival drilldowns, complete materialization readiness,
  dependency/egress gates, old backend removal, replacement baseline/reset, and coordinated live
  acceptance remain outstanding. The goal remains active; no deployed services or dev data changed.

### Bounded current frontier drilldowns

- Added typed `GET /collections/{id}/items` and `GET /frontier/items/{id}` reads. Collection items
  paginate by interest UUID (up to 100 per response) and explicitly identify this as identity order,
  not dispatch order. Retired/missing current state returns 404; historical arrivals remain a separate
  lake concern rather than an implicit retained-state history claim.
- Item views link acquisition identity to request interest, budget state, traversal context, supplied
  observation identity where present, and bounded caller previews. SQL filters collection visibility
  before per-acquisition windowing; private associations cannot affect public previews or overflow.
  Private acquisitions and private collection item routes return no public data. Caller previews omit
  collection intent and are limited to ten plus an overflow flag.
- Reads select the required ORM columns and a scalar observation-ID JSON projection; they do not load
  full outcome payloads, frozen capture requirements, selection checkpoints, or raw provider errors.
  Background output is restricted to causal parent/rule fields. Public middleware allows only the new
  GET routes. API handlers neither dispatch work nor contact/provision NATS.
- Pending items distinguish global pause, persisted retry floor, and awaiting scheduler evaluation.
  A known eligibility floor is not a runnable-state or dispatch promise. Start estimates explicitly
  remain unavailable because current reads do not observe domain permits and dispatch capacity.
  Evidence receipt and query readiness remain separate.
- Four repository tests plus an API authorization/bounds test passed. An isolated real Postgres check
  exercised windowed shared callers, JSON scalar extraction, UUID pagination, and proved reads did not
  dispatch work; its schema was removed. `make check` passed: 454 backend tests (22 opt-in skips),
  16 SDK tests, and package/frontend checks/builds. Diff whitespace passed.
- Frontend drilldown links, historical arrival reads, and full live velocity/eligibility remain pending,
  alongside readiness proofs, runtime dependency/egress gates, old backend removal, replacement
  migrations/reset, and deployed end-to-end acceptance. No deployed state changed.

### Collection/frontier browser navigation

- Both collection detail screens now include ten-item cursor pages of current request interests,
  with explicit identity ordering, admission time, budget state, traversal parent/rule/depth, and
  acquisition status. Refresh-first and previous/next controls do not invent a queue position.
- Added public `/frontier/{id}` and admin `/frontier/items/{id}` detail screens over the typed API.
  They display visible caller previews with links back to collections, physical attempts started,
  background causal links, retry floors, unavailable estimate reasons, and evidence receipts without
  claiming query readiness. Mode `acquired` is rendered as “Acquisition requested” so it does not
  describe queued work as a completed capture. Observation links open bounded SQL with validated IDs.
- Public proxies expose only the exact GET item routes and preserve the public service credential.
  Both clients poll through React Query with bounded fetch deadlines, show read errors/staleness,
  and provide explicit refresh. Historical collections do not request deleted current interests.
- Admin/public typechecks, lint, tests (10/18), and builds passed before final wording polish;
  final typecheck/lint/build verification follows below. Store-backed intercepted Chrome exercises
  passed the full existing scenarios plus collection → item → caller navigation in both applications.
  Desktop item screenshots were inspected; both owned local test servers were stopped. The check
  remains isolated browser evidence, not deployed integration or live network acquisition.
- Historical observation/arrival provenance screens, complete live status/velocity, verified readiness,
  dependency/egress gates, old backend removal, schema reset, and deployed acceptance remain pending.
- Final admin/public typecheck, lint, and build passed after the wording changes. Diff whitespace
  passed. No backend code, deployed service, or development data changed in this browser step.

### Durable collection arrivals

- Added bounded `GET /collections/{id}/arrivals` backed by immutable DuckLake fulfillment evidence.
  Reads share the existing collection-history catalogue slot, have a ten-second interrupt deadline,
  return at most 100 rows plus one cursor sentinel, and select bounded scalar fields. No operational
  retention table or second catalogue connection owner was added.
- Cursors bind collection identity to descending decision time and fulfillment ID. Private lineage
  is filtered before ordering/limiting, private observation joins are excluded, and inconsistent
  embedded/outer definition visibility fails closed. Oversized URLs/rules and duplicate page evidence
  fail rather than producing misleading output. Historical pagination is not a pinned snapshot;
  refresh-newest is explicit for later commits.
- Fulfillment commit and base-observation commit are separate. A row may precede its observation;
  a later read exposes observed outcome/status/effective URL when the base commit appears. Neither
  milestone claims materialization readiness. Current intent whose definition has not committed
  returns an explicit waiting-definition page, while absent/private collection identities remain 404.
  Storage outages remain 503 with Retry-After rather than false empty results.
- Both collection detail screens now show cursor-paged durable arrivals for current and historical
  requests, with independent stale/error handling and bounded polling. The public proxy exposes only
  the exact read route with its existing public service credential. SQL observation links remain
  separate from verified queryability.
- Thirteen real DuckLake lineage/history tests passed, including three new arrival cases covering
  tied-timestamp pagination, reads without operational state, late observation commits, private
  filtering, inconsistent visibility, and oversized evidence. History-slot/cursor and API tests also
  passed. Isolated Chrome checks passed in both apps with historical arrival fixtures; mobile
  screenshots were inspected and owned servers stopped. No live acquisition or deployment occurred.
- Full-suite final verification follows below. Live velocity/status, historical observation-to-caller
  drilldowns, complete readiness proofs, runtime dependency/egress gates, old backend deletion,
  replacement migrations/reset, and deployed acceptance remain outstanding.
- Final `make check` passed: 459 backend tests (22 opt-in skips), 16 SDK tests, and all package/
  frontend checks and builds. Diff whitespace passed. The goal remains active.

### Public crawler Live

- Added public `GET /frontier/live` and `/live`, with current public queue/domain activity from bounded
  Postgres aggregates and immutable recent/velocity evidence through the existing catalogue read
  slot. Current reads return ten domains, five pending items, and five retained successes; private
  work is filtered before counts and limits. An isolated Postgres check proved aggregation and
  timezone behavior without dispatching work.
- DuckLake measures committed attempt starts, successful/failed captures, and request fulfillments
  separately over explicit 60- and 300-second windows, including global totals and up to ten domains
  per window. Retries are physical attempts; sharing/reuse remain request results. Imported/private
  observations and future/out-of-window events are excluded from crawler rates. Rates are explicitly
  committed-evidence measurements and may lag acquisition; they are not instantaneous worker speed.
- Recent retained and historical successes merge by observation ID, preferring confirmed commits,
  then select the latest five. Ingestion and query readiness remain distinct. Catalogue read failure
  retains current public activity and reports unknown history/rates instead of zeros. The SQL reader
  has a ten-second interrupt deadline; it adds no telemetry store, NATS consumer, or scheduler action.
- The UI includes a domain table, oldest public queue admission, window selector, rates/errors,
  latest captures, and links from pending previews to frontier items. Pending previews are explicitly
  oldest-admission samples rather than scheduler/FIFO predictions; start estimates remain unavailable
  because domain permits and dispatch capacity are not observed. Started-state counters do not assert
  worker liveness. Public navigation now includes Live and uses a small-screen grid to avoid crowding.
- Four Live repository/history tests and two API tests passed, including real DuckLake empty-window
  zeros, privacy/import filtering, distinct counting grains, time windows, bounded previews, outage
  degradation, and deduplicated recent captures. `make check` passed: 465 backend tests (22 opt-in
  skips), 16 SDK tests, and package/frontend checks/builds. Public typecheck/lint/build passed again
  after the navigation layout adjustment. Diff whitespace passed.
- Chrome used real isolated store and temporary DuckLake data behind intercepted API transport to
  exercise windows, private filtering, dependency degradation, stale recovery, pending-item links,
  and mobile overflow. Screenshots were inspected; final mobile navigation verification follows below.
- Complete materialization readiness, historical observation-to-caller drilldowns, dependency/egress
  gates, SDK read additions, old backend removal, baseline/reset, and deployed live acceptance remain
  outstanding. These measurements have not been benchmarked against production-sized remote history.
- Final Live browser checks passed after the mobile navigation adjustment; screenshot inspected and
  owned Next test server stopped. No deployed services or development state were changed.

### SDK visibility reads

- Added typed collection `items()` and `arrivals()` methods plus module-level reads, and frontier
  `item()`/`live()` reads. The standalone SDK carries its own HTTP models and imports no backend
  package. Responses retain source, commit/readiness unknowns, scoped ordering, caller previews,
  explicit velocity intervals, and missing-history reasons.
- UUID paths and page/cursor bounds validate before I/O. Item/arrival responses must preserve the
  requested identity. Cursors remain opaque; no automatic unbounded iteration or failed-read retry
  was added. Storage errors propagate instead of becoming empty history. README examples document
  the new surfaces and the difference between recorded rates, current work, and query readiness.
- SDK tests increased to 20. The actual ASGI transport test exercises store-backed items, frontier
  detail, pending definition arrivals, Live history failure/recovery, and committed arrival models.
  All tests passed. A newly built wheel installed into an isolated Python 3.11 environment passed
  all 20 SDK tests with no backend package installed; temporary environment removed, no publication.
- `make check` passed: 466 backend tests (22 opt-in skips), 20 SDK tests, and all package/frontend
  checks and builds. Diff whitespace passed. No deployment or development-state reset occurred.
- Readiness investigation confirmed that existing applied-batch records contain totals rather than
  visit membership. Materialization's batch context does include every selected visit, including
  visits without documents, and all registered projection files commit atomically with the marker.
  The next step is a direct per-observation proof tied to the active generation; ingestion receipts
  and aggregate batch totals alone remain insufficient. Readiness, historical observation provenance,
  dependency/egress gates, old backend deletion, migrations/reset, and deployed acceptance remain open.

### Active-generation observation readiness

- Added the auto-discovered `visit_readiness` projection. Every selected visit emits membership,
  including failed/no-document/non-HTML observations. Membership files and existing applied markers
  commit in the same transaction as all projection outputs; full generation activation includes it.
- Added bounded (100 identities, ten-second interruption) readiness reads using one statement
  snapshot for visible ingestion evidence, active registry state, visit membership, and HTML content
  presence. Missing, private, inconsistent, or mismatched state remains unknown.
- Found and fixed a cross-batch correctness gap: a non-owning visit can commit its links and membership
  before the separate shared-content owner commits DOM/JSON-LD. Readiness now waits for the active
  content root marker too. A real two-batch DuckLake test commits the non-owner first and proves this.
- Arrivals and Live recent captures expose verified/pending/unknown states; SDK and corresponding
  UI types/rendering accept all three states. Current collection summary/item readiness remains
  conservative pending its own bounded integration.
- Five real DuckLake readiness tests pass, covering atomic rollback/replay, absent content,
  reverse-order shared-content commits, private/hidden/mismatched generations, inconsistent data,
  arrival integration, and Live transitions. Full checks and rendering verification recorded below.
- This changes the materialization registry digest and requires the coordinated rebuild/reset;
  no running service or existing development lake has been changed.
- Follow-up full `make check` passed: 471 backend tests (22 opt-in skips), 20 SDK tests at
  that invocation, and all frontend checks/builds. The added three-state SDK test subsequently
  passed in the isolated Python 3.11 wheel suite (21 tests total; no backend installed).
- Browser rendering verified all three readiness states on the built public Live page, mobile
  width, and no page errors; screenshot inspected. Responses were intercepted typed fixtures,
  not deployed services. The owned preview server was stopped.
- Remaining goal scope is unchanged: collection/item readiness integration, durable reverse
  provenance, dependency and egress gates, old execution removal, replacement baseline and
  coordinated state reset, and actual CDP-to-query acceptance are still required.

### Frontier-item readiness integration

- Current collection item pages and individual frontier drilldowns now enrich visible observation
  identities through the same bounded catalogue read slot used by arrivals/history/Live. The
  PostgreSQL read transaction closes first; queued work skips catalogue access entirely.
- Each page issues one deduplicated readiness read for at most 100 visible observations. A positive
  lake check can confirm evidence even before its operational receipt arrives. Busy/unavailable
  catalogue reads preserve current work and return unknown readiness with an explicit reason.
- Public/private filtering precedes the readiness call; tests verify inaccessible observations do
  not enter the catalogue read. Administrator reads preserve their authorized visibility scope.
- Both frontends show readiness on observed item cards and detail pages, including a specific
  catalogue-unavailable explanation. The standalone SDK accepts verified/pending/unknown item states.
- Added API tests for pending/verified transitions, delayed receipts, outage degradation, and
  pre-read visibility; added shared-slot and input-bound tests. Verification results follow below.
- Full `make check` passed: 473 backend tests (22 opt-in skips), 21 SDK tests, and all frontend
  checks/builds. The isolated Python 3.11 SDK wheel suite also passed all 21 tests.
- Built public/admin drilldowns passed browser checks for unknown, pending, verified, and
  catalogue-unavailable readiness; mobile screenshots inspected and no page errors. Browser
  API responses were typed fixtures. Both owned preview servers were stopped afterward.
- Collection-level readiness and historical reverse provenance remain outstanding, along with
  the previously recorded dependency/egress, old-backend removal, baseline/reset, and deployed
  end-to-end acceptance work. No deployment or state reset occurred in this step.

### Removed graph execution and replaced the control baseline

- Removed the orphaned graph APIs/adapters, editable graph/schedule models and services, graph
  delivery/outbox/store/navigation/progress/runtime, and schedule execution. None were registered
  by the active API/crawler. Removed obsolete tests and moved shared policy/navigation test fixtures
  to `frontier_fixtures`; retained the frontier's actual navigation and selection tests.
- Removed the old graph-only policy-variance helper and its tests, which had no remaining runtime
  caller, and the unused croniter dependency. Automatic quality experiments remain future work in
  the destination contract; the frontier continues freezing configured content policies directly.
- Replaced all prior Alembic revisions with frozen `20260907_0001`, generated against an isolated
  empty PostgreSQL schema. It contains exactly ten current tables: collections, two policy tables,
  five frontier tables, and two materialization control tables. No migration bridge or old graph
  table remains in registered metadata.
- Setup now initializes the frontier singleton along with default policies. Repeated setup does
  not replace operator settings. The isolated actual PostgreSQL test proves baseline upgrade,
  zero metadata drift, initialization preservation, and complete downgrade; all 21 frontier
  PostgreSQL transaction/race tests also pass.
- Updated architecture, lifecycle, deployment, and repository guidance to describe the replacement
  runtime. Removed the superseded crawl-plan guide. The destination requirements remain unchanged.
- Initial full check identified one additional graph-metrics API test; removed it with its deleted
  API. A fresh full check is running. Import-level CrawlRecord/ingest.crawls cleanup and physical
  lake versioning remain outstanding; no services were restarted and no existing state was reset.
- The replacement source tree has no remaining graph-runtime, graph/schedule control, or croniter
  references. `git diff --check` passes. Backend verification now runs 436 tests (23 opt-in skips);
  the reduction removes tests for the deleted execution model, rather than skipping their failures.
  SDK tests remain at 21. Final frontend build completion is recorded after its live handle exits.
- Full `make check` completed successfully, including frontend checks and production builds.
  No low-depth live crawl was run against the mixed pre-cutover lake; that remains an explicit
  acceptance gate after import/lake contract replacement and coordinated setup.

### Observation-only external ingestion and physical cutoff

- Removed CrawlRecord, ingest.crawls, crawl ingestion jobs/results, catalogue crawl read/write APIs,
  and the unused queue resume wrapper. Frozen ingestion jobs accept only visit or lineage evidence
  and reject superseded payload fields. External HTML publishes exactly one visit evidence job;
  no synthetic graph run or collection is manufactured.
- External observation identity now hashes the canonical JSON source tuple (system, dataset,
  source_record_id) under its UUID namespace. Added a newline-boundary collision regression test;
  exact replays remain stable, and independent source records stay independent.
- Bumped physical catalogue version to 5.0.0. Removed the old registry-digest ALTER compatibility
  branch; setup expects the replacement schema after the coordinated disposable-state reset.
- Removed remaining unused graph-scoped navigation names, event IDs, edge-selection package
  serialization/contracts, and run-prefix deletion. Active frontier navigation generation/load/store
  remains unchanged. Source scans find no old crawl record, graph-run navigation, or edge package.
- Updated external-import/schema documentation and public SQL completion/reference content to
  remove crawl_id and document the three explicit lineage views independently of observations.
- Initial full check passed 437 backend tests (23 opt-in skips) and 21 SDK tests plus frontend
  checks/builds. A final full check includes the navigation/shim cleanup and public SQL guidance.
- No real development data was reset and no service was restarted. Full collection readiness,
  reverse historical provenance, dependency/egress gates, and coordinated live acceptance remain.
- Verified all seven public SQL reference entries (complete ordered columns and types) against
  DESCRIBE results from a freshly bootstrapped temporary DuckLake catalogue. TIMESTAMPTZ is
  normalized to DuckDB's canonical TIMESTAMP WITH TIME ZONE spelling for this comparison.
- Final full `make check` passed: 437 backend tests (23 opt-in skips), 21 SDK tests, frontend
  checks and production builds. `git diff --check` passes. Actual CDP acquisition, deployed
  ingestion/materialization, and coordinated state reset remain unperformed acceptance gates.

### Pre-acquisition delivery connectivity gate

- Added a five-second bounded check of the existing ingestion connection, JetStream stream, and
  result KV handles before domain admission or physical-attempt authorization. The probe provisions
  no infrastructure, publishes no job, and does not wait for ingestion/materialization progress.
- Known delivery failure releases the unstarted physical reservation and returns the acquisition to
  a thirty-second retry. Persisted `defer_reason` explains ingestion_delivery_unavailable in existing
  item views; it clears on physical start. The replacement baseline includes this nullable column.
- Tests cover read-only probe operations, disconnected/missing delivery dependencies, authorization
  ordering, repeated outage deferrals with zero physical budget charge, and reason clearing after
  recovery. The actual PostgreSQL baseline round-trip still matches ORM metadata.
- The initial full check exposed an integration fixture without the production pipeline's queue
  handle. Updated it to share one queue fixture between pipeline and runtime; final checks follow.
- This gate proves only observed delivery connectivity. Storage-write readiness, CDP availability,
  full egress protection, and the previously recorded acceptance gates remain outstanding.
- Final full `make check` passed: 441 backend tests (23 opt-in skips), 21 SDK tests, and all
  frontend checks/builds. The installed NATS client exposes the asynchronous KV status and bounded
  flush methods used by the preflight. `git diff --check` passes. No deployed services/state changed.

### Public-destination validation across all capture sources

- Extracted public-address validation into the acquisition boundary and reused it for description
  discovery. Every capture delivery now validates its URL before domain pacing or physical start,
  covering direct, corpus SQL, follow SQL, and background admissions through the same handler.
- Public-address rejection terminally cancels only the matching unstarted generation, detaches its
  interests, and releases physical allowance. No observation or attempt is invented when capture
  never began. Existing prior attempts still use ordinary terminal evidence handling.
- DNS outages return the unstarted acquisition to thirty-second retry with an explicit waiting
  reason. Four outstanding lookups per event loop bound resolver work; cancellation/timeouts keep
  the underlying resolver task charged until completion. Literal private/loopback/link-local,
  multicast, and reserved destinations are rejected without DNS; mixed DNS answers fail closed.
- Frontier item API/SDK/frontend contracts expose terminal_reason so the rejection remains legible.
  Tests cover DNS classes and capacity, capture authorization order, generation fencing, and zero
  physical budget charges after rejection. This is preflight, not a DNS-rebinding/egress sandbox.
- CDP-wide egress enforcement, storage/CDP readiness, collection summary readiness, durable reverse
  provenance, and coordinated reset/live acceptance remain required.
- Added and passed a real PostgreSQL race between destination rejection and physical start:
  exactly one transition wins and the rejected path retains zero attempt charge. All 22 live
  PostgreSQL frontier tests pass in isolated schemas; no deployed records were touched.
- Final backend checks passed 446 tests (23 opt-in skips at that invocation) and 21 SDK tests.
  Frontend completion and the isolated SDK wheel result are recorded below when their handles exit.
- Final full `make check` completed successfully, including frontend checks/builds. The isolated
  Python 3.11 SDK wheel suite passed all 21 tests. `git diff --check` passes. No deployed
  acquisition, service restart, state reset, or provider discovery was performed.

### Control-lock timing and authorization expiry

- Found fourteen transitions sampling process time before waiting for the shared control row:
  control edits, discovery checkpoints, ingestion receipts, admission, dispatch/dispatch-next,
  unstarted deferral, collection settlement, physical start/retry/renewal/recovery, cancellation,
  and completion. Those transitions now read PostgreSQL clock_timestamp after lock acquisition.
  Explicit deterministic test timestamps remain supported; SQLite uses precise local time only
  after lock acquisition. The existing background clock helper is now named for its wider use.
- Added real PostgreSQL contention tests: a lease that expires while start waits on the control
  lock cannot authorize capture; a new dispatch receives its complete lease after the wait ends.
  All 24 PostgreSQL frontier tests pass in isolated schemas.
- This changes authorization timing, not immutable capture timestamps or observed attempt usage.
  Full repository verification is running. No deployment, acquisition, or state reset occurred.
- Follow-up audit found a second possible wait for independently locked collection/outbox rows.
  Collection release, successful receipt acceptance, and receipt deferral now sample time after
  those row locks too. A real PostgreSQL test proves release cannot accept a collection claim
  that expires while its row is locked. All 25 PostgreSQL frontier tests pass in isolated schemas.
- The first full repository check passed. A final check includes the independent-row timing fixes.
  Remaining delivery claim/publication clock paths still need the broader timing audit; this step
  does not claim every lease in the system has been verified under contention.
- Final full `make check` passed: 450 backend tests (27 opt-in skips), 21 SDK tests, frontend
  checks and builds. The separate 25-test live PostgreSQL suite passed. `git diff --check` passes.
  No deployment or data reset occurred; the continuous crawler goal remains incomplete pending
  the remaining dependency/egress, visibility, timing, and coordinated live-acceptance work.

### Completed frontier delivery lease-clock audit

- Replaced parameter-time conditional outbox updates with short row-locked transitions. Publication
  acknowledgement and retry release check the current claim token and expiry against PostgreSQL
  wall-clock time after acquiring the row; stale deliveries cannot publish or defer a newer claim.
- Collection, outbox, and ingestion-receipt claim selection now uses database time. Their bounded
  rows are materialized/locked before a fresh clock reading grants the lease, so query/connection
  latency does not subtract from the new lease. Recovery previews also use the database clock.
- No `now = now or datetime.now(...)` default remains in FrontierStore methods. Explicit test
  clocks remain supported; this does not claim all timestamps in other services share one clock.
- Added actual PostgreSQL contention tests for publication and retry expiry, plus a test that
  forbids process-clock use during collection/delivery/receipt claim operations. All 27 live
  PostgreSQL frontier tests pass in isolated schemas. Full repository verification is running.
- Dependency/egress completion, remaining visibility work, and coordinated reset/live acceptance
  remain outstanding; no deployed services or existing development data changed.
- Final full `make check` passed: 452 backend tests (29 opt-in skips), 21 SDK tests, and all
  frontend checks/builds. The separate live PostgreSQL suite passed all 27 tests. `git diff --check`
  passes. No deployment, data reset, provider discovery, or physical acquisition occurred.

### Standard CDP connection before physical authorization

- Capture deliveries establish a bounded standard CDP connection before domain pacing reservation
  and `begin_attempt`. The acquisition API requires that connected browser and cannot create a
  second connection internally. Its owner closes the connection on success, deferral, failure,
  and cancellation, with a five-second cleanup bound.
- Known connection failures defer unstarted work for thirty seconds with `cdp_unavailable` and
  release its physical allowance. Local Playwright driver loss remains fatal to the owning runtime.
  Connection establishment happens after logical dispatch: this does not yet prove the specification's
  dependency-health gate before participant budget consumption. Storage readiness and the complete
  pre-dispatch dependency gate remain outstanding.
- A real isolated local Chrome test passed standard-CDP handshake/disconnect and verified that
  connection establishment created no page or navigation. Unit tests cover unavailable endpoints,
  fatal driver loss, cancellation cleanup, and the handler ordering/identity of the connected browser.
  The physical deadline now explicitly tests a hanging page creation on that connected browser.
- The first repository check found an obsolete timeout fixture (`new_context` instead of `new_page`);
  it was corrected. The subsequent backend run passed 456 tests (29 opt-in skips), and 21 SDK tests
  passed. All seven handler tests also passed after strengthening the connection-order assertion.
  Frontend checks/builds are still being verified for this change.
- No deployed services, existing development data, external provider discovery, or physical page
  acquisition changed. Egress enforcement, collection readiness aggregates, durable reverse lineage,
  cleanup, coordinated cutover, and full live acceptance remain required.
- Full `make check` completed successfully, including frontend checks/builds. The three acquisition
  boundary tests passed again after the fixture naming/cleanup edit, and `git diff --check` passed.
  All verification processes and the isolated Chrome instance have exited.

### Dependency checks before transactional dispatch

- Dispatch now checks existing ingestion handles, actual repository write/read/delete access, and
  standard CDP connection establishment before invoking `dispatch_next`. Successful checks have a
  five-second freshness window; failure pauses that replica's dispatch for thirty seconds. Local
  Playwright driver loss still exits the runtime. No database lock spans these probes.
- The acquisition pipeline owns one storage probe task, shared by concurrent callers and retained
  across five-second waiter timeouts/cancellation. Results expire five seconds after completion,
  including when the original caller stopped waiting. Shutdown drains outstanding storage work.
  The handler also checks storage before domain/physical authorization and exposes `storage_unavailable`.
- Probes use small unique transient `runtime/probes/` objects and verify content before deleting.
  Janitor reclamation scans bounded batches of abandoned probes older than two hours. Disk tests
  verify a real write/read/delete roundtrip leaves no probe objects. Immutable evidence is untouched.
- Five storage tests pass (real disk/cache, cancelled shared work, recovery, corrupt read, janitor
  retention). Added a real repository-transition runtime test for outage-before-consumption and
  recovery, plus a handler test proving storage failure precedes physical authorization.
- First full verification found only a wrong module import in the new runtime test; corrected it.
  Final repository verification is running. These local checks do not prove remote S3 availability,
  guarantee future dependency health, or yet expose pre-dispatch outage reasons through Live.
  Coordinated state reset, deployed acquisition, and the remaining full acceptance scope are pending.
- The repository-transition test now passes: during an injected storage outage the acquisition
  remains `queued`, request counters stay `(reserved=1, consumed=0)`, and recovery performs one
  dispatch/consumption. Its initial expectation used the wrong state label (`pending`); corrected
  to the actual contract. All three assembled-runtime tests pass.
- A sixth storage test proves repeated caller timeouts retain the same unfinished probe and allow
  its eventual result to be consumed. All six storage tests pass. Final `make check` is running
  against these corrected tests; no remote services or existing development data changed.
- Final full `make check` passed: 464 backend tests (29 opt-in skips), 21 SDK tests, and all frontend
  checks/builds. The corrected three-test runtime suite and six-test storage suite passed separately.
  `git diff --check` passes. All verification processes exited; no deployed cutover or physical crawl
  was performed. Next dependency-observability work must expose the pre-dispatch pause through Live;
  process logs alone are not the complete operator contract.

### Live dispatch dependency visibility

- Connected the dispatch health state to existing crawler heartbeats. The reporter distinguishes
  checking, recently passed probes, and waits for ingestion delivery, storage, or CDP; ready status
  expires independently of dispatch-loop progress. Runtime recovery assertions verify blocked-to-ready.
- API startup reconciles/reuses the existing crawler presence bucket and owns a bounded reader.
  Live polls at most 128 reports within two seconds, serializes remote reads, and caches two seconds.
  It excludes stale, malformed, and future reports and marks truncation. No worker hostname, raw
  exception, private URL, or private-work counter enters the public health aggregate.
- Added matching SDK and public UI contracts. Live explains passed checks, waits, checks in progress,
  stale-report exclusions, and unknown availability separately from authoritative frontier counts.
  These observations do not promise a free lane, immediate dispatch, or global worker totals.
- API tests passed. Presence tests cover heartbeat publication, mixed health, stale/expired reports,
  absent and unavailable KV, bounded preview, concurrent readers, and malformed/future records.
  Full checks and rendered-browser verification are underway; coordinated cutover is still pending.
- The full check exposed an SDK HTTP test fixture missing the new startup-owned presence reader;
  the fixture now supplies it. All 472 backend tests (29 opt-in skips) and 21 SDK tests passed
  subsequently. Eight presence/health tests also pass after refining expired checks to `unknown`
  instead of `checking`; fresh heartbeats cannot keep stale dependency readiness alive.
- Rendered the built public Live page at 1280px and 390px with mocked API responses for observed,
  missing, and unavailable worker reports. All assertions passed; no page errors or horizontal
  overflow occurred. Inspected the mobile screenshot. The isolated browser and preview server were
  stopped. This verifies UI rendering, not deployed NATS/crawler connectivity.
- A final full check is running after the `unknown` refinement. Previous full checks passed; no
  existing development state, deployed service, physical crawl, or provider discovery was changed.
- Final full `make check` passed against the refined implementation: 472 backend tests (29 opt-in
  skips), 21 SDK tests, and frontend checks/builds. `git diff --check` passes. All verification and
  preview processes have exited. Remaining acceptance still includes egress enforcement, aggregate
  request readiness, durable reverse lineage, cleanup completion, coordinated reset, and live crawl.

### Complete-collection readiness and cutover preparation

- Added one-statement DuckLake collection proofs over terminal outcomes, complete fulfillment sets,
  observation evidence, active visit membership, and shared HTML content markers. Missing evidence
  remains unknown; verified incomplete materialization is pending; complete proof returns its active
  generation and observation time. Reads accept at most 100 collections and have a ten-second interrupt.
- Current detail/list and historical detail responses use the existing serialized catalogue reader
  after releasing PostgreSQL reads. Catalogue failure preserves current status with unknown readiness.
  SDK and both UIs accept verified/pending/unknown collection readiness and expose check time.
- The first full check passed 474 backend tests (29 opt-in skips), 21 SDK tests, and frontend builds.
  API readiness integration tests passed separately. Added shared-content batch-order assertions;
  their initial fixture used an invalid outcome label, corrected to the actual `budget_reached` contract.
- Cutover inspection found unused graph/scheduler environment defaults, Compose anchors, chart fields,
  and performance constants. Removed those superseded settings; Compose validation passes. Updated
  the stale default CDC checkout path. Final checks include these cleanup changes.
- At that validation run, the sibling CDC checkout differed from the then-pinned source revision.
  Superseded by the signed community package installation and revision checks in `docs/EXTENSION_DEVELOPMENT.md`.
  Exported the exact pinned commit 9fec6a7decf417b1c5b68cbd0a51af187e4e2a99 to a temporary build context
  without modifying the sibling checkout. Built replacement core image successfully:
  sha256:6bfce3f94773c53c5dc898db739684d2657ac69dcded887993a47bdbde7ae522.
- Replacement UI images are building. No container cutover or data reset has happened yet. No CDP
  listener is currently present on local port 9222; acceptance needs a controlled CDP service before
  starting a physical crawl. Existing local containers still use their old image/schema contracts.

### Coordinated local cutover and first live crawl

- Completed the authorized local reset: stopped old Periplus containers and external LakeDucktor,
  removed old/orphan Periplus containers (including the superseded web container), and removed all
  five disposable control/NATS/lake/object volumes plus the old Compose network together.
- Started the rebuilt core, public, and admin stack with `docker compose up -d --wait --no-build`.
  Setup completed on fresh state; API, query, four ingestors, four materializers, crawler, janitor,
  public/admin, PostgreSQL, NATS, and S3 all became healthy. No old deliveries target the new schema.
- Real Live reads exposed a mock-hidden NATS issue: KV.get does not populate Entry.created, and
  KV.keys creates an ephemeral watcher. Replaced both with read-only stream subject metadata and
  last-message reads using server timestamps. No polling consumer is provisioned. Rebuilt/restarted
  the API, verified real worker health through the deployed public proxy, and reran all checks:
  475 backend tests (29 opt-in skips), 21 SDK tests, and all frontend checks/builds passed.
- The actual configured CDP endpoint is Stolosio on the local network (`/v1/connect`), not the
  example localhost:9222 endpoint. Its connectivity check passes. Kept background allocation zero,
  set dispatch concurrency to one and start rate to six/minute for acceptance, then ran the actual
  installed Python 3.11 SDK smoke example against https://example.com/ with depth zero and one page.
- Collection bcc15854-de73-434f-9d70-cab7e0b6b8e2 settled with one consumed page, one supplied page,
  and zero failures. The fresh lake initially had no active materialization generation, correctly
  reported as unknown readiness. Started rebuild 84b7a56c-cda5-4041-9516-932cbd7a3c6f: one batch,
  one source visit, fourteen output rows, completed with no error and activated its registry.
- The installed SDK smoke passed its real read-only SQL proof over fulfillment, observation,
  content object, and HTML root membership. Collection readiness now reports true for the active
  generation. Actual accounting is one physical attempt, 2,394ms measured capture time, zero
  outstanding reservations, and zero background attempts. This is client elapsed time, not billing.
- Verified deployed public collection details and Live in a real browser without mocked responses;
  complete query readiness and the successful capture are visible. Screenshots are under /tmp.
  Reattached LakeDucktor to the recreated Compose network and restarted it; it is healthy.
- The full goal remains incomplete: Stolosio/provider-wide egress enforcement, durable reverse
  lineage, cleanup completion, broader sharing/background/restart/fault acceptance, and the final
  requirement-by-requirement audit remain. No claim of general untrusted-target egress safety is
  made from this single fixed benign URL. No commit, push, or external publication occurred.
- Visual inspection also caught a remaining UI rough edge: the current-item readiness read reported
  temporary unavailability while collection and durable-arrival proofs were verified. This is consistent
  with the API's single busy-rejecting catalogue read slot, but concurrent page polling needs acceptance
  follow-up so a healthy catalogue does not routinely look unavailable. It does not invalidate the
  completed SDK SQL proof or the verified collection proof.

### Live sharing, reuse, and readiness contention

- Against the deployed stack, paused dispatch, admitted two compatible public requests for the same
  fixed URL, and verified they referenced one acquisition before resuming. Both consumed one request
  unit and received one supplied page; physical attempts increased by exactly one. A third request
  reused that completed result, became query-ready, and added no physical attempt. These later visits
  were materialized by the live path without another rebuild.
- Evidence: collections 6651ab51-7930-4692-998c-61efdcc11726 and
  f18034b2-c633-4a31-9937-c4e5f726477a shared acquisition
  95e58630-4971-41ad-9b6c-e1e971b2f050; reuse collection
  0ebf4070-8d4b-486f-8c21-67544b5126f3. Actual global attempts increased from one to two.
- The screenshot's readiness inconsistency came from application read admission: overlapping page
  requests were rejected immediately even when individual catalogue reads were healthy. Added a
  bounded local waiting group to the existing single catalogue slot: at most eight admitted reads,
  at most two seconds waiting, and still one executing read. Cancellation drains active work and
  removes queued waiters without releasing another reader's ownership. No SQL rewrite or schema
  change was used to mask this admission behavior.
- Targeted tests and full checks are running, and the API fix image is built. Live private-isolation
  and queued-cancellation acceptance are also running using recorded IDs, with background disabled.
- Live private isolation passed: collection 439ad1ca-4631-4c18-9e5e-c394ead4bbd0 performed its own
  capture despite a reusable public result, became query-ready, and both its collection and acquisition
  returned 404 to public callers. It did not reuse public acquisition state.
- Live queued cancellation passed: two public requests shared one queued acquisition; cancelling
  283f107b-d842-4e83-85da-53e8c86d3999 released its reservation and consumed zero units. Remaining
  caller 28418188-be4e-433c-8c1c-566f0b94c15d completed with one consumed/supplied page. Together these
  private/cancellation cases increased physical attempts from two to four, exactly as expected.
- Full `make check` passed: 477 backend tests (29 opt-in skips), 21 SDK tests, and frontend checks/builds.
  Rebuilt and deployed the API read-admission fix. A browser check of the real deployed collection page
  now verifies all three levels together: complete collection readiness, current-item readiness, and
  durable-arrival readiness. The former temporary-unavailable message is absent. No API responses
  were mocked. Verification scripts exited and `git diff --check` passes.
- Background allocation is still zero. Broader ingestion/restart/fault and background acceptance,
  durable reverse lineage, retention completion, provider-wide egress, and final requirements audit
  remain outstanding. The tests here do not imply those remaining gates have passed.

### Downstream recovery and durable observation drilldown

- Live collection 3089dd3a-87c2-49b1-8e5d-e85ecd4d15c9 acquired the fixed benign URL with
  all ingestors and materializers stopped. It settled with one supplied result while ingestion
  remained unconfirmed. Restarting the crawler and ingestors committed evidence without another
  capture; materialization remained explicitly pending until materializers restarted. The live
  CDC path then verified complete query readiness. Global attempts increased from four to five,
  exactly once; the request consumed one page. All downstream replicas were restored. This tests
  delayed consumers and restart after durable acceptance, not abrupt death at every commit boundary.
- Added a bounded reverse historical read, `/frontier/observations/{id}/lineage`, through the existing
  catalogue read owner. It returns capture causes separately from acquired/shared/reused result
  associations, with visible parent, collection, rule, and policy identities. It reads only immutable
  evidence; no operational rows, new persistence, or new delivery lane are required. Pages explicitly
  describe ingestion lag, have a ten-second query deadline and at most 100 entries, and use cursors
  bound to observation and service visibility. Absent/private observations return 404; unavailable
  catalogue reads remain retryable 503, never an empty successful page.
- Real DuckLake tests verify paging over shared/reused/background lineage, independent of any control
  database, and verify late definition commits and private-parent/observation filtering. API tests cover
  the public service capability, admin scope, bounds, and sanitized unavailable responses. Both browser
  applications and the SDK now expose this drilldown; full checks and deployed browser verification
  are in progress. Live links now lead to the observation drilldown rather than only a SQL draft.
- Full final `make check` passed: 481 backend tests (29 opt-in skips), 23 SDK tests, and both
  frontend checks/builds. Admin lint and ten admin tests also passed. Rebuilt and deployed the API,
  public app, and admin app; all three are healthy.
- Deployed public API pagination and real browser inspection verified observation
  95e58630-4971-41ad-9b6c-e1e971b2f050 has two original capture causes and three result users;
  the reuse request appears only as a result user. The private observation is still public-404.
- Exercised production cleanup methods with an accelerated cutoff on the disposable local acceptance
  data, after verifying zero pending/active work and background allocation zero. Durable commit and
  ownership gates remained intact. Eight settled collections and five acquisitions were reclaimed;
  current collection/acquisition counts became zero, while cumulative physical attempts stayed five.
  This did not change normal one-hour retention configuration or delete immutable lake evidence.
- After cleanup, the shared current-item endpoint returns 404, all three associated collections resolve
  from history with one supplied page and verified complete readiness, and the same public provenance
  API/browser checks pass without mocks. The rendered page explicitly distinguishes absent current
  state from available durable provenance. Screenshot: `/tmp/periplus-durable-lineage.png`.
- Remaining gates include transient empty-directory reclamation, broader background/SQL/fault live
  acceptance, provider-wide egress verification, and the complete requirements audit. Historical
  survival is now verified through actual control-row retirement; it is no longer only a fixture claim.

### Local directory retention and live background allowance

- File repository deletion now prunes only empty ancestors and stops at its repository root.
  Repeated deletion can finish pruning after an interrupted unlink. Concurrent writes retry the
  bounded mkdir-to-temporary-file race; once the temporary file exists, empty-directory pruning
  cannot remove its parent. Tests verify sibling and immutable evidence preservation, root retention,
  interrupted deletion, and a real filesystem race injected before temporary-file creation.
- Full `make check` passed: 483 backend tests (29 opt-in skips), 23 SDK tests, and frontend checks/builds.
  This source change affects filesystem repositories; the running local stack uses S3. The filesystem
  change has not yet been rolled into all running worker images.
- Live collection 02790669-06dc-42a5-9346-b0db7a11ce36 captured example.com at depth zero and settled
  with one consumed/supplied page. Background navigation was retained because allocation was enabled.
  An exhausted background allowance correctly prevented selection too; the test then used a domain
  pause to hold a separately admitted candidate while leaving physical allowance available.
- Set allocation to zero, released the domain pause, and verified no background dispatch for twenty
  seconds with a pending candidate and sufficient allowance. Re-enabling allocation authorized exactly
  one background attempt after request completion. Global attempts increased from five to seven (one
  request and one background); background attempts increased from zero to one. The completed request
  retained one consumed/supplied page and verified query readiness. Background allowance was capped
  to one additional attempt throughout the continuation test.
- Restored the prior global controls with background allocation zero and restored the default domain
  pause to false. Experiment records are `/tmp/periplus-live-background.json` and its matching log.
  These checks establish live independent continuation and zero-allocation dispatch behavior; they
  do not alone prove restart seen checks, trap resistance, or every fault boundary.
- Verified the background result itself: observation 15919552-8779-4362-8eda-1a030f869e9b captured
  `https://iana.org/domains/example` successfully in one attempt, committed at source snapshot 65,
  and is query-ready. Its public durable provenance has one background cause, no collection, and
  parent 337ad773-bd81-47bc-ac40-360ae98c36fd. The retained selection reason identifies historical
  snapshot 63 and query c95c1d8d-b171-42d3-b138-67907d3d49d9.


### Seen eligibility after retirement and worker restart

- Rebuilt the core image with the filesystem retention fix and deployed it to all core process roles.
  API, query, crawler, four ingestors, four materializers, and janitor became healthy. No old worker
  image remains from the earlier cutover, and no schema or materialization registry change was needed.
- After the historical-check lease expired, retired the preceding background experiment's one
  collection and two acquisitions with the production cleanup gates and an accelerated local cutoff.
  Navigation objects had already been reclaimed by the janitor. Verified zero retained acquisitions
  and zero active/pending work before the new experiment; immutable lake history was preserved.
- Following the worker restart, submitted collection b96704e3-2eb9-4262-b208-1e15890ae8c7 for
  example.com, depth zero, page limit one, fresh acquisition. It became query-ready. Background
  selection rediscovered `https://iana.org/domains/example` and recorded `status=seen`,
  `reason=lake_observation`, with no acquisition identity. This decision could not rely on a retained
  IANA frontier row because all previous acquisitions had been removed.
- Physical attempts increased from seven to eight only for the explicit request. Background attempts
  stayed at one despite available allowance for another. No second IANA acquisition was admitted.
  Restored controls with background allocation zero. The guarded experiment and evidence are
  `/tmp/periplus_live_seen_restart.py`, `/tmp/periplus-live-seen-restart.json`, and its matching log.
- Remaining independent gates are wider SQL/fairness/abrupt-fault acceptance, actual provider network
  isolation, estimate coverage, and the final full contract audit. The provider isolation question has
  been raised with the user while work on the remaining Periplus-owned gates continues.

### Live SQL seeds and divergent traversal

- Submitted two requests with the same bounded, parameterized corpus seed SQL while dispatch was
  paused. Both admitted example.com into one queued acquisition. One follow SQL selected the IANA
  link; the other selected no links. Both used max depth one, page limit two, and fresh acquisition.
- The first verification script incorrectly expected a seed checkpoint after all seed admissions;
  that checkpoint is intentionally replaced by retained seed provenance. Inspected and continued
  the existing recorded requests without resubmitting them or altering runtime behavior.
- Collections c2f3b47b-4811-4d07-9461-ff31b5bd417d and fbee84b5-ea7a-4fd5-bb3b-44e781e09d07
  shared observation 55b844ca-e3ce-43d0-851a-7dc18db96537. Only the first acquired IANA child
  86a907d5-436a-4e49-bba1-77030939d83e, with the shared observation as parent and depth one.
  Both reached complete query readiness: the first consumed/supplied two pages, the second one.
  Global physical attempts increased from eight to ten, correctly serving three request results.
- Retained seed provenance identifies source snapshot 78, separate query IDs, selection times,
  and matching candidate digests. Evidence is in `/tmp/periplus-live-sql-traversal.json` and
  `/tmp/periplus-verify-sql-traversal.log`. The original test's failed checkpoint expectation is
  preserved in its log and is not presented as a successful restart check.
- A separate capacity/restart test submitted collection 68af6893-9dcf-4354-bcc0-b2b777f96775 with
  two SQL-selected URLs and admission capacity one while dispatch was paused. Exactly one URL was
  admitted and the remaining checkpoint retained cursor one, both frozen URLs, query ID
  b7798b33-2c61-4c39-805b-cbea8529efca, and snapshot 95. No physical attempt started during the hold.
- Restarted the crawler and verified the checkpoint was unchanged. After resuming dispatch, the
  request acquired both pages, consumed/supplied exactly two, and reached complete query readiness.
  Terminal seed provenance retained the original query ID, snapshot, and selection time. Global
  attempts increased from ten to twelve only. Restored the original controls; background is zero.
  Evidence: `/tmp/periplus-live-sql-checkpoint.json` and its matching log. This verifies actual
  partially admitted SQL resumption, separately from the earlier completed-selection case.


### Abrupt capture-process failure

- Submitted collection 5206a092-1de4-4b00-b906-5476b08d76df for one fresh example.com page,
  with background disabled, concurrency one, and allowance for at most two additional attempts.
  A separate process observed physical authorization directly in control Postgres; then SIGKILL
  stopped the crawler. Acquisition e2ea3726-519a-40b3-b521-9a5b8b503ba7 still had no accepted
  outcome after the process died. It had attempt count one, generation one, and its original
  execution lease ending at 08:56:27 UTC. No lease timestamp was edited to accelerate recovery.
- Restarted the crawler and allowed its normal two-minute lease fence to expire. It recovered and
  captured the page successfully. Immutable terminal evidence retains two attempts: the first is
  uncertain with unknown finish and measured time, retaining its 15,000ms bound; the retry succeeded
  with 2,203ms measured client capture time. Neither value is claimed as provider billing.
- The collection consumed/supplied exactly one page and reached complete query readiness. Global
  starts increased from twelve to fourteen, with zero outstanding physical reservations. A direct
  public SQL count verified exactly one terminal observation under the acquisition identity.
- The initial verification query used `?::UUID`, which the query parser rejected with 422. Repeated
  only that read with the bound parameter directly; no request or capture was resubmitted. The
  original failure log is retained alongside `/tmp/periplus-verify-abrupt-crash.log` and the completed
  `/tmp/periplus-live-abrupt-crash.json` evidence record.
- Restored the original controls, kept background zero, and verified all local services healthy.
  Added FRONTIER_ACCEPTANCE.md as the current evidence matrix. The goal remains incomplete pending
  remaining contract/audit gaps; this experiment does not imply exactly-once physical CDP execution
  or prove every publication/commit boundary.

### Current wait constraints and catalogue validation

- Audit found item reads using a generic scheduler-wait reason even when a current domain pause or
  persisted pacing constraint explained the wait. Added bounded current-policy lookups using the same
  policy specificity as dispatch; reads still happen under the control read lock, without remote I/O.
- Item views now distinguish domain pause/pacing/capacity, global pacing/dispatch capacity, and physical
  allowance exhaustion. Timing floors include only version-matching domain hints; policy changes
  invalidate the old hint. Both UIs say eligibility floor rather than retry floor. No start promise
  or unsupported estimate range was introduced; estimate coverage remains an explicit open gate.
- Full `make check` passed 485 backend tests (29 opt-in skips), 23 SDK tests, and frontend checks/builds.
  The subsequently added capacity/privacy regression test also passed in the seven-test targeted item
  suite. It confirms capacity explanations expose neither other caller identities nor occupancy counts.
- Rebuilt and deployed API/public/admin. A live public read for request
  be3625f1-016e-445e-aebb-6eddd1c73221 correctly reported `domain_paused` rather than generic scheduler
  waiting. Cancelled it before dispatch; consumed/reserved pages were zero and global attempts stayed
  fourteen. Restored the domain and global controls, with background zero. Evidence is in
  `/tmp/periplus-live-wait-reason.json` and its log.
- Ran the named `make catalogue-check` against the local deployed lake: it reported
  `Periplus catalogue is valid.` Log: `/tmp/periplus-frontier-catalogue-check.log`.

### DuckDB anonymous parameter casts at the query boundary

- Investigated the `?::UUID` rejection exposed by abrupt-crash verification. DuckDB 1.5.5 accepts
  this syntax; SQLGlot 30.12.0 tokenizes the adjacent characters as one inherited QDCOLON operator.
  Classified this as parser behavior, not a public schema or query-plan problem.
- Corrected the query validator's DuckDB tokenization by omitting that inherited operator keyword.
  All other DuckDB parser/tokenizer rules remain inherited; original SQL and bound parameters execute
  unchanged. The same validator serves corpus seed queries. No global library mutation or statement
  rewriting was added. Actionable upstream evidence is recorded in UPSTREAM.md; nothing was published.
- Real DuckLake tests compare adjacent and explicit cast results/types, preserve literals/comments,
  exercise UUID predicates, and retain rejection of multiple statements and private namespaces.
  Seed-client coverage verifies unchanged SQL/parameters and frozen URL/snapshot/query provenance.
- Full `make check` passed 488 backend tests (29 opt-in skips), 23 SDK tests, and both frontend
  checks/builds. Deployed the updated API, query and crawler images; these services are healthy.
- Against the deployed query service, the original crash-test SQL using `?::UUID` now executes
  unchanged and counts exactly one terminal observation. The real seed client also executes
  `SELECT ?::VARCHAR AS url` and receives the correct URL with a source snapshot/query identity.
  This verification starts no acquisition. Log: `/tmp/periplus-parameter-cast-live.log`.


### Conditional start ranges and publication-marker rollback

- Added bounded conditional next-start ranges using three to twenty retained public successful
  starts under matching domain, acquisition requirements, and global/domain policy versions.
  Ranges include calculation time, five-second expiry, sample count, and explicit uncertainty.
  Missing or stale worker evidence, competing work, unsupported callers, and insufficient or
  overdue samples produce specific unavailable reasons. This is an observed wait range, not a
  confidence interval or dispatch reservation; historical queue contention is not recorded.
- Updated API, SDK, and public/admin types and start-window components. The full check passed
  490 backend tests (29 opt-in skips), 23 SDK tests, and both frontend checks/builds; two targeted
  view tests also passed against isolated real Postgres schemas. Deployed API/public/admin images.
- Live collection `6aa962cf-a912-45c8-93de-8818e3c97ad6` supplied three sample captures.
  Target collection `c828bb41-8a4a-4747-b48a-b5f831ddb330` received a non-null API range and
  became query-ready. Physical starts increased from 14 to 18, as bounded in advance. Controls
  were restored. Evidence: `/tmp/periplus-live-start-estimate.json` and corresponding `.log`.
  Browser verification timed out: this run does not prove that the range rendered in the UI or
  that the actual start fell inside the forecast. Admission estimates remain unfinished.
- Added an isolated real Postgres fault-injection test for successful capture publication followed
  by a flushed but uncommitted publication-marker update. The forced commit failure rolls the
  marker back, lease expiry permits replay with the same payload/message identity, and duplicate
  delivery cannot authorize a second physical attempt. The actual relay and repository methods
  execute; JetStream acknowledgment is simulated, so this is not a live broker-crash experiment.
  The targeted test passed: `/tmp/periplus-puback-commit-test.log`.
- Stolosio source inspection confirms its hostname blocklist is not its network isolation boundary.
  The inspected Compose and Helm configurations contain no egress isolation policy; host/platform
  configuration remains unknown. The broader network-isolation verification is an interpretation
  of FRONTIER.md's egress-protection requirement, not an additional prescribed product feature.


### Admission waiting transparency

- Current collection views now expose remaining frozen candidate entries across seed and follow
  selections, a preview of at most five URLs, oldest known selection time, elapsed selection age,
  and a specific reason an admission forecast is unavailable. Counts are candidate occurrences,
  not promised new pages: deduplication, scope rules and budgets still apply at admission.
- The preview uses only the visible collection's checkpoints. Follow candidates are extracted as
  scalar URLs without loading their complete checkpoint or navigation payloads. Aggregate counts
  are scoped to the same current collection. Unresolved selections have no invented candidate
  count or wait duration. Missing selection timestamps leave the oldest wait unknown.
- Follow checkpoints now record selection time. Public/admin collection detail pages and the
  Python SDK consume the new admission contract. No admission forecast is synthesized: retained
  state does not yet record comparable capacity-wait intervals for this estimator.
- Targeted current-view tests passed in SQLite and isolated real Postgres schemas, including mixed
  seed/follow partial selection and private-request exclusion. Logs:
  `/tmp/periplus-admission-view-tests.log`, `/tmp/periplus-admission-postgres.log`.
- The preceding publication-marker change passed full `make check`: 491 backend tests (30 opt-in
  skips), 23 SDK tests, and both frontend checks/builds. The added Postgres commit-failure case
  passed separately rather than relying on its default skip.
- Scraper network isolation is now a deployment follow-up the user is handling with an isolated
  VLAN. This records ownership, not verification that the VLAN/firewall configuration is deployed.

- Admission changes passed full `make check`: 493 backend tests (30 opt-in skips), 23 SDK tests,
  and both frontend checks/builds. API, crawler, public and admin images were deployed healthy.
- Live collection `b49c4445-a834-4995-9edf-1283fcf78a00` admitted one URL while dispatch was
  paused and the admission limit was one. The public API and actual browser showed the second
  frozen URL, its selection age, and `frontier_admission_capacity`. The screenshot was inspected.
  The request was cancelled before restoring original controls; physical starts remained 18.
  Evidence: `/tmp/periplus-live-admission-wait.json`, corresponding `.log`, and
  `/tmp/periplus-admission-wait.png`. This verifies the waiting display, not admission forecasting.


### Selection priority and bounded-window audit

- The final source audit found that capture dispatch honored request priority, but resolution and
  admission service claims used only FIFO due times. Due collection claims now rank by service
  timestamp minus priority seconds. Priority remains bounded to -10 through 10, so its relative
  advantage is at most twenty seconds. Each completed service pass refreshes its timestamp;
  dependency backoff and existing leases are filtered before priority ranking.
- A real isolated Postgres test verifies higher-priority due service, exclusion of not-yet-due
  service, and eventual older low-priority service after high-priority requests are refreshed.
  Log: `/tmp/periplus-selection-priority.log`. Full `make check` passed 494 backend tests
  (31 opt-in skips), 23 SDK tests, and both frontend checks/builds. API/crawler images deployed healthy.
- Reviewed dispatch's 64-candidate window against tests with 70 saturated/delayed or 80 paused
  same-domain URLs preceding eligible work. Domain constraints are applied before that window.
  Request rotation and bounded priority preference are covered by store tests; the background
  allocation test checks the complete `[request, request, request, background]` sequence over
  twenty contested dispatches at 25 percent allocation, then verifies spare-capacity use.
- Delivery is bounded by a 32 MiB stream with reject-new overflow, 128 unacknowledged messages,
  and configured local capture lanes capped at 48. The committed outbox retains unpublished work
  when the delivery stream is full. Background candidate selection examines at most 1,000 links,
  selects at most four per host, and caps the round-robin batch at 64 URLs / 64 KiB. Initial trap
  policy rejects calendar/session paths, repeated/deep paths, session/facet query keys, and excessive
  pagination. These are bounded initial controls, not a claim to recognize every possible trap.

### Start-window rendering and expiry

- A second live experiment completed four samples and one target (collections
  `5693d035-6d6d-46ff-ae23-19347a6a0137` and `9e43a365-964d-427f-9142-2b4ea13175dd`).
  All five acquisitions succeeded under matching requirements and policy versions. Physical starts
  increased from 18 to 23; original controls were restored. The target started about 2.2 seconds
  after admission. The probe observed no supported estimate, so this experiment does not verify
  live estimate rendering. Its capture work was inspected, not resubmitted after the failed probe.
- Independently verified the deployed public and admin start-window components using an explicit
  intercepted API fixture. Both render the range, sample count and uncertainty, then remove the
  range after its five-second expiry. Screenshots were inspected. This UI evidence is distinct
  from the earlier real API range observation; it does not validate forecast accuracy or real
  queued/evidence state. No captures were started by this UI test.
  Evidence: `/tmp/periplus-start-window-ui.json`, corresponding `.log`, and
  `/tmp/periplus-start-window-public.png` / `/tmp/periplus-start-window-admin.png`.


### First-admission forecasting

- Added a bounded current-request timing observation and Alembic revision `20260907_0002`.
  Submission uses the control database clock; first interest creation freezes its timestamp in
  the same transaction. Admission replay does not change it. Priority edits and intervening
  crawler control changes invalidate comparability. The observation retires with current state.
- Direct public single-URL requests can receive a conditional first-admission range from three to
  twenty recent same-hostname requests under matching control version, priority and reuse-age
  setting. Prediction scope is explicitly `first_admission`, not batch or collection completion.
  Discovery, deadlines, later admissions, capacity limits, insufficient/stale observations and
  changed controls produce explicit unavailable reasons. Calculation time, five-second expiry,
  sample count and uncertainty accompany each range. No separate telemetry service/table was added.
- The initial live probe exposed an incorrect browser-readiness dependency in the estimator.
  Admission now requires recent crawler-process presence, independently of CDP readiness, matching
  the runtime's existing admission/dispatch separation. Real Postgres regression coverage verifies
  that a blocked CDP dependency does not suppress an otherwise supported admission range.
- Migration upgrade/downgrade matches current models in an isolated real Postgres schema. Three
  estimator tests pass on real Postgres, and collection API / SDK integration checks pass. Applied
  the migration locally, rolled core/frontends healthy, and deployed the API presence correction.
- Full `make check` passed 497 backend tests (31 opt-in skips), 23 SDK tests, and both frontend
  checks/builds. Logs: `/tmp/periplus-admission-presence-check.log`,
  `/tmp/periplus-admission-estimates-postgres.log`, `/tmp/periplus-admission-migration.log`.
- Live target `da3de79c-bd23-41a3-a18c-203ac048b35c` received a first-admission range from three
  real preceding admissions through the deployed API. All four requests were admitted with capture
  dispatch paused, then cancelled and original controls restored. Physical starts remained 23.
  Evidence: `/tmp/periplus-live-admission-presence.json` and corresponding `.log`.
- Both deployed frontends rendered and expired first-admission ranges with an explicit API fixture;
  screenshots were inspected. The fixture verifies presentation and expiry, independently of the
  real API estimate observation. Evidence: `/tmp/periplus-admission-window-ui.json`, corresponding
  `.log`, and `/tmp/periplus-admission-window-public.png` / `-admin.png`.
- The remaining collection-view audit identified a concrete gap: collection responses have queued
  totals and per-item wait explanations, but do not yet expose aggregate runnable/deferred counts,
  oldest queued wait or a last-progress timestamp. Those explicit request-view requirements remain
  open; this ledger does not declare the complete frontier goal achieved.

## Final sharing and reuse boundary review

Reviewed `capture_identity`, `admit`, dispatch participant freezing, `needs_navigation`, and
navigation/acquisition retirement against FRONTIER's equivalence and reuse requirements. The key
contains normalized URL, the complete content policy snapshot (including its identity/version),
visibility/access context and private collection isolation. Domain pacing is intentionally checked
at execution rather than treated as content equivalence. Acquisition requirements are stored at
creation and retained for dispatch/capture; request-specific traversal remains on interests.

Existing tests in the current passing suite cover shared budgets, first context including a later
shallower path, cancellation before/after dispatch, no in-flight joining, successful recent reuse,
zero-age fresh capture, expired reuse, missing/evicted branch navigation and current effective-URL
exclusions. Previously recorded real Postgres retirement/reuse and concurrent context tests cover
transaction ordering; live divergent traversal and reuse provide acquisition-path evidence. A
branch interest pins retained navigation through unsettled selection; retirement must revoke the
reference in the same serialized state boundary before deleting the object. No additional code
change was required by this review.

## Collection queue and progress summaries deployed

Current collection API/SDK contracts and both request detail screens now expose scoped runnable,
deferred and unknown-eligibility counts, oldest admitted waiting time, bounded constraint counts,
and the last execution-progress timestamp. Runnable counts use stored policies and fresh worker
readiness; permits and capacity are rechecked at capture start. Current exclusions that need
candidate inspection remain unknown rather than falsely runnable. Retry waits are counted;
unadmitted candidates remain in the separate admission summary.

`20260907_0003` adds the operational progress timestamp. Admission, selection, dispatch, physical
start, retry outcome, fulfillment and settlement update it transactionally. Duplicate admission,
frozen-selection replay, stale cursor advancement, reads, waiting reasons, priorities and pause edits
do not make progress appear newer. Existing submission/dispatch/completion timestamps remain valid
observations when a current row has no dedicated timestamp.

Verification:
- Two new behavioral tests pass on SQLite and real isolated PostgreSQL; they cover scoped queue
  partitioning, delayed retry, missing readiness, pause, oldest wait, private isolation, actual
  selection/admission progress and replay/polling invariance.
- The complete three-revision Alembic upgrade/downgrade round trip matches current model metadata.
- `make check` passes 499 backend tests (31 opt-in skips), 23 SDK tests, and both frontend checks,
  tests and builds. Admin lint also passes. Logs: `/tmp/periplus-collection-queue-check.log`,
  `/tmp/periplus-collection-queue-postgres.log`, `/tmp/periplus-collection-progress-migration.log`.
- Migration applied locally; rebuilt core/public/admin images rolled to all process roles, all
  healthy (`/tmp/periplus-collection-queue-rollout.log`).
- Real collection `f66b9781-a4ef-403c-96bf-0e5f1e88a817` admitted two URLs while globally paused.
  API and strict SDK validation reported zero runnable, two deferred, zero unknown, with
  `crawler_paused`. Both deployed frontends rendered those actual responses without fixtures.
  Screenshots `/tmp/periplus-collection-queue-public.png` and `...-admin.png` were inspected.
  Across reads/browser visits the oldest wait increased while last progress stayed unchanged.
  The request was cancelled and controls restored; physical attempts remained 23.
  Evidence: `/tmp/periplus-collection-queue-live.json` and `.log`.

## Final submission and SQL contract review

The public form's `publicCollectionSpec` constructs the same `CollectionSpec` consumed by the
Python API and SDK: internal/external/both choices become predicates over `nav.links`, with depth,
page limit and sections as explicit intent. The form limits public depth to 0–2 and pages to 1–1000;
Python independently enforces those role limits, public visibility/access context and zero initial
priority. The proxy transports requests and errors without executing discovery or traversal.
`collection-submission.test.ts` covers the mapping and form bounds; API tests cover malformed rules,
transport bounds, absent seeds, capacity errors, private identities and submission idempotence.

Seed selection freezes source snapshot/query ID, parameters through frozen intent, selection time,
candidates and cursor before admission. Link selection uses each interest's context, a retained
navigation package and the request's frozen SQL. Standalone DuckDB disables external access and
extension loading, locks configuration, and enforces row/byte/memory/time limits. Selection tests
reject catalogue/file/environment access and multiple statements, and reject overflow explicitly.
Previously recorded live SQL traversal and checkpoint restart remain the end-to-end evidence.

## Retrospective scheduler alternative comparison

The final audit did not find a recorded established-frontier comparison before implementation.
The following review closes the decision documentation now; it is retrospective and does not claim
that the requested pre-implementation sequencing occurred.

Scrapy is the established alternative considered. Its default scheduler offers request priorities,
duplicate filtering and optional disk queues; its downloader-aware queue favors less-active slots.
These are useful scheduling primitives ([official scheduler documentation](https://docs.scrapy.org/en/latest/topics/scheduler.html)).
Its persistent job directory is scoped to one crawl job and is not designed to be shared between
jobs ([official job persistence documentation](https://docs.scrapy.org/en/latest/topics/jobs.html)).

Integration assessment: Periplus would still need transactional request interests, per-request
budget reservations, capture equivalence, participant freezing, fenced physical attempts, the
Postgres outbox, lake-only durable seen evidence, immutable shared lineage and page-local SQL
checkpoints. A custom Scrapy scheduler could delegate to those same transactions, but its default
queue/deduplication state cannot replace them without another authoritative ledger. Its engine and
downloader would also need adapters around the existing standard-CDP acquisition and NATS permits.
This is an architectural inference from the documented scheduler interface and Periplus's required
contracts, not a benchmark or a claim that Scrapy cannot be extended.

Decision: retain the direct scheduling-core replacement. It keeps the existing crawler process,
Postgres/NATS ownership, CDP boundary and lake ingestion without an additional engine lifecycle.
The cost is maintaining our own fairness, claim and recovery code; the contention tests and live
recovery evidence in this ledger are required precisely because a framework does not supply those
guarantees. Adopting Scrapy would be worth reconsidering for a concrete downloader/spider workflow,
not merely to wrap these already-required domain transactions in its scheduler interface.

## Final recovery, lineage and throughput review

The current PostgreSQL concurrency suite passes all 29 tests in isolated schemas
(`/tmp/periplus-final-frontier-postgres.log`). This separately executes the tests skipped by the
ordinary suite. The migration chain and isolated real-Chromium exclusions have their separately
recorded runs. Cancellation/dispatch is tested over twelve fresh races; consumed versus released
accounting follows the actual winning transaction. Deadline and lease checks sample time after
blocking locks, and allowance contenders cannot both spend one remaining physical unit.

Reviewed outcome acceptance: frozen observation evidence, navigation, physical-cost settlement,
interest fulfillment and outbox insertion commit in one control transaction. A rejected/stale
outcome cannot publish. After acceptance, duplicate completion compares immutable evidence and does
not enqueue another observation. Before acceptance, the live SIGKILL experiment demonstrates the
bounded uncertain-attempt contract. The Postgres PubAck/marker-failure experiment proves published
work can be replayed with identical delivery identity without authorizing another physical attempt.

Reviewed ingestion: each durable visit/lineage commit precedes result publication and ACK. A failed
result write NAKs; an expired receipt republishes the same frozen evidence. Visit and lineage portions
of a mixed batch may commit separately, so reconciliation checks exact durable identities before
retrying. Duplicate visits and late fulfillments do not rerun capture or create fabricated visits.
Materialization's applied marker and proof share the file-registration transaction; its existing
lost-ACK and precommit recovery tests cover the independent materialization milestone.

Reviewed seen/cleanup ordering: before remote lookup, a bounded 120-second check pins candidate IDs
and an unknown snapshot under the same control serialization used by cleanup. Only a complete
snapshot-labelled result can proceed. Cleanup retains evidence markers until their receipt is no
newer than every live check's snapshot; absent snapshots block cleanup. Expired checks cannot admit,
and admission rechecks current acquisitions under the control lock. Failed/malformed lake responses
remain unavailable, never unseen. The live post-cleanup/restart experiment confirms durable seen
eligibility after operational records have actually disappeared.

Reviewed public catalogue: `web.observation` joins only its document and filters public visits;
collection definition/outcome, fulfillment and causal reasons are separate grains. Fulfillment
records are appended independently after sharing/reuse. Durable forward/reverse readers enforce
visibility and bounded pagination before returning links/counts. The earlier real browser/API
retention experiment remains evidence that those links survive deletion of current execution rows.

A fresh read-only direct DuckLake measurement reports 21 successful public observations, 21
successful attempts totaling 56,891 client-measured milliseconds, and one uncertain attempt charged
at its 15,000 ms bound (`/tmp/periplus-final-capture-cost.log`). This is measured capture work across
controlled experiments, not elapsed-session throughput, private activity, provider billing or a
production capacity benchmark. The low-depth captures and live CDC tests establish real ingestion
and materialization, separately from request settlement.

Documentation review corrected obsolete graph/coverage descriptions in root/public/admin READMEs,
removed the old four-view/custom-query-extension claim, corrected terminal query port/token guidance,
and aligned the cutoff/deployment language. Active runtime/API/SDK/source and topology searches find
no superseded scheduling contract; the intentionally excluded historical reference is unchanged.

## Final completion-signal correction and acceptance

The final quality audit found that acquisition steps already measured before/after element counts,
text size, links, scroll height and iteration counts, but `step_records` discarded them. They now
remain under the distinct `measurements` key in the existing step JSON envelope, alongside action
version/configuration, duration and stopping reason. No history rewrite, new queue, relation or
compatibility path was added. The acquisition-boundary test verifies the exact observed values
survive into frozen evidence. Both request interfaces now explicitly distinguish successful capture
from unverified usefulness/completeness.

`make check` passes 499 backend tests (31 opt-in skips), 23 SDK tests and both frontend checks/builds
(`/tmp/periplus-frontier-quality-check.log`). Core/public/admin images were rebuilt and deployed to
all roles, healthy (`/tmp/periplus-frontier-quality-rollout.log`).

Fresh depth-zero, page-limit-one collection `4ecdba98-9e24-4bdd-aa2f-922fb7fc4aac` produced observation
`9a6ee781-6e0e-44cf-94cb-d52772ab308b`, reached verified active-generation query readiness, and
increased physical starts from 23 to 24. The existing dispatch limit remained one and background
allocation zero; no controls were changed. Direct read-only DuckLake inspection confirms all three
completion steps retain measurements:
- dynamic wait: 826 ms, three iterations, stable;
- scroll: 935 ms, three iterations, bottom stable;
- expand: 23 ms, zero iterations, no control.
Each retained before/after counts of 12 elements, 129 text characters, one link and 600px height.
These are measured capture signals, not a claim that reaching bottom proves completeness.
Evidence: `/tmp/periplus-live-completion-metrics.json` and
`/tmp/periplus-live-completion-metrics-lake-verified.log`. The initial verification query had an
operator-precedence mistake; the corrected read queried the same observation and did not recapture.

The acceptance index now maps every destination-contract section and all twelve criteria to
implementation and evidence. No required software implementation work remains. The documented
network deployment follow-up and retrospective sequencing deviation are not hidden by this status.

Final exit checks: `make catalogue-check` reports a valid deployed catalogue
(`/tmp/periplus-frontier-final-catalogue.log`), and `git diff --check` passes. The control API reports
24 physical starts, zero pending/dispatched acquisitions and zero remaining attempt/time
reservations. Dispatch limit is one, background allocation zero, and the crawler is not paused.
No commits, pushes or external publications were made.
