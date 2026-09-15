# ClickHouse simplification audit

Audit date: 2026-09-15. Scope: the uncommitted `codex/clickhouse-experiment`
worktree, after the first successful capture-to-public-query E2E.

## Verdict

**Yes. We implemented a working ClickHouse path, but have not yet delivered the
clean replacement we discussed.** The branch combines a small new materializer
with substantial old ingestion coordination, an obsolete materialization lifecycle,
two catalogue descriptions, and UI/deployment surfaces that still expect DuckLake.
Some leftovers are active dependencies and observable failures, not merely unused files.

This does not invalidate the narrow E2E result. It does mean that passing that
flow and `make check` is insufficient evidence of a coherent replacement.
[E2E_RESULT.md](E2E_RESULT.md) explicitly limits that checkpoint;
[EXIT_CRITERIA.md](EXIT_CRITERIA.md) remains the wider acceptance contract.

The next work should consolidate this vertical slice before adding projections,
indexes, another transport, or a general storage abstraction.

## Evidence and limits

I traced the API/process entrypoints, both durable consumers, evidence and material
writers, frontend callers, cleanup ownership, model registration, build dependencies,
and Compose versus Helm configuration. I also made authenticated, read-only requests
to the running local API. No services were stopped, production stores accessed, or
runtime code changed during this audit.

Observed local results:

| Request | Result |
| --- | --- |
| `GET /healthz` | 200 |
| `GET /sql/metadata` | 500 |
| `GET /operations/storage` | 404 |
| `GET /operations/materializations/runs` | 404 |
| `GET /documents/by-content/<UUID>/content` | 404; the route is absent, independently confirmed in API registration |

The audit did not run a new outage campaign, rebuild, retention deletion, Helm
deployment, or capacity benchmark. Failure-path conclusions below distinguish
source evidence from runtime reproductions. Previous successful tests are recorded
in E2E_RESULT; no full test rerun was warranted for this documentation-only audit.

Current Python inventory, including shared and still-useful code:

| Directory under `packages/periplus/src/periplus/` | Files | Lines |
| --- | ---: | ---: |
| `ingestion` | 19 | 3,327 |
| `materialization` | 32 | 5,889 |
| `platform/catalogue` | 24 | 3,211 |
| `retention` | 9 | 731 |
| `query` | 16 | 2,296 |
| `operations` | 23 | 1,640 |

These are inventory counts, **not a claim that those lines are all deletable**.
Imports, DTO reuse, dynamic projection discovery and build-time validation prevent
classifying dead code by filename or import counts alone.

## Findings, in priority order

### 1. P1 — The exposed API and product no longer agree with the running backend

`query/http.py:57` still injects `CatalogueControl` for `/sql/metadata`.
`platform/catalogue/control.py:120` raises when that object is missing.
`entrypoints/api.py:75` still registers the router, although its new lifespan does
not create that control object. This is the reproduced 500 above.

There are also frontend callers for removed routes:

- Admin `src/hooks/use-storage.ts:9` calls `/operations/storage`.
- Admin `src/hooks/use-materialization.ts:11` calls `/operations/materializations/runs`.
- Public `src/app/api/content/[id]/route.ts:8` calls `/documents/by-content/{id}/content`.
- Public `src/components/query-workbench.tsx:110` still offers experimental mode;
  `query/service.py:81` rejects a non-stable service configuration.
- Public `src/app/docs/page.tsx` still advertises six views, `search()`, DuckDB
  execution, and statements such as `DESCRIBE`. The new service installs four
  relations and accepts SELECT/set-operation statements through its validation path.

**Cleanup:** make the supported surface explicit. Replace metadata with the
ClickHouse public contract, and either implement required product capabilities or
remove their exposed controls and documentation. Missing functionality is not
solved by leaving unreachable implementations behind. Do not add fallback routes.

**Exit check:** exercise every route used by the shipped UI against real services;
all advertised examples execute or produce an intentional, documented response.
Health and frontend compilation alone cannot establish this.

### 2. P1 — Disabling the old janitor also removed necessary maintenance

The experiment Compose does not deploy the janitor. Its old loop in
`operations/janitor.py:95` combines unrelated responsibilities:

- bounded navigation and abandoned probe object cleanup;
- pruning completed frontier execution while retaining customer collection records;
- acquisition cleanup and expired write-claim cleanup;
- private query-history expiry;
- DuckLake logical retention, publication protection release and raw deletion.

Repository call-site searches found the first group of cleanup invocations in this
janitor, with no replacement process owning them. `FrontierStore.cleanup_collections`
at `crawl/runtime/frontier_store.py:477` already preserves customer records while
pruning execution payloads, but the running experiment never schedules that cleanup.
This creates a growth problem even while destructive retention is intentionally off.

Raw publication protections are also still created by ingestion. Their release is
in `retention/publications.py`, through the old retention path. Simply enabling the
old janitor wholesale is not a safe fix. In addition,
`retention/runtime.py:35` treats collection-row existence as a protection; retaining
customer records permanently requires separating their existence from an active
service entitlement before reusing that logic.

**Cleanup:** restore a small janitor with explicit, separately observable bounded
jobs for transient execution, navigation/probes, expired claims and private query
history. Keep raw deletion disabled until the ClickHouse protection protocol is
implemented and tested. Publication protection release belongs in that protocol;
do not delete protection objects merely because they look old.

**Exit check:** run cleanup twice on completed and active fixtures. Completed
execution disappears, customer intent/counters remain, live work survives, expiry
is enforced, and each job reports examined/removed/blocked/error counts.

### 3. P1 — Ingestion still uses the old state machine and error taxonomy

`ingestion/consumer.py` is 516 lines and `ingestion/queue.py` 598 lines.
`ingestion/ingestor.py:52` provisions result KV storage and operation leases, then
runs catalogue lane monitoring/presence alongside consumers. The queue persists
full jobs in state records, while the stream and frontier outbox also hold the work.

`PreparedIngestion` now wraps only a job. `commit_prepared_batch` in
`ingestion/service.py:67` loops over independently committed evidence identities.
The surrounding batch preparation/claim/result machinery therefore no longer buys
the former shared DuckLake transaction. It still has behavior that callers rely on,
so deleting the wrapper alone is not the entire cleanup.

There is a concrete semantic mismatch: `platform/catalogue/operations.py` classifies
DuckDB/Postgres failures but not `ClickHouseError`. A direct classifier check returned
false for a ClickHouse capacity failure. `ingestion/consumer.py:335` consequently
counts those errors toward processing failures instead of taking the infrastructure
unavailability branch. Reconciliation can postpone final failure while the server
remains unavailable; I did not reproduce an actual outage-induced dead letter.
Nevertheless, the error classification is wrong for the new engine.

**Cleanup:** give the ingestor a direct bounded consume → validate → commit/reconcile
→ ACK path. Separate transient infrastructure errors, uncertain writes, and permanent
invalid evidence. Keep operators able to inspect/retry permanent failures.

Before removing result KV, replace the frontier receipt dependency explicitly:
ClickHouse receipts establish durable evidence, while Postgres records the exact
operational transition/counters. JetStream carries pending work. Do not replace KV
with a second permanent generic workflow ledger. Remove a job-level lease only after
proving the remaining exact write claims and idempotent control transitions cover
its current responsibility.

**Exit check:** duplicate delivery, concurrent delivery, lost insert response,
server outage and recovery, poison input, and interruption around frontier receipt
accounting. Counts must remain exact; infrastructure downtime must not exhaust a
malformed-input retry budget.

### 4. P1 before deployment — Helm still deploys the old architecture

Compose has the ClickHouse topology, but `charts/periplus/templates/query.yaml`
still configures DuckLake and renders stable plus experimental query services.
`workers.yaml` still deploys the old janitor. The query template does not supply the
new ClickHouse query credentials. The chart therefore does not describe the runtime
that passed the E2E.

**Cleanup:** convert deployment configuration to the chosen runtime directly,
including process roles, credentials, health, resources and monitoring. Remove
obsolete chart values and environment settings. Do not publish this as a deployable
replacement while the chart still encodes the old topology.

**Exit check:** render the chart and validate each process's actual environment
contract; test that topology in an isolated deployment before production use.
This audit inspected source, not a live Helm deployment.

### 5. P2 — The old materialization framework is largely bypassed, not removed

The live `materialization/materializer.py` is 101 lines and calls the new storage
path directly. The old planner, batch executor, generation cutover, CDC connection,
registry, projection writers and lifecycle remain in the tree. They do not provide
background rebuilds for the new materializer.

`materialization/models.py` still declares runs, batches, state and applied-batch
records. `platform/postgres/models.py` still registers those models. New writes do
not use that lifecycle. The old HTML projection also repeats the element ancestry,
sibling and text-span calculation now in `materialization/html_content.py`.

**Cleanup:** retain the DOM parsing/encoding/link primitives and canonical projection
semantics; remove superseded DuckLake execution and projection storage paths after
extracting their remaining consumers. Do not port the old framework line for line.
Use Alembic for removal of obsolete control schema, with disposable development
reset where appropriate; no compatibility tables or migration bridge are needed.

Background rebuild remains a separate required implementation: a bounded source
scan, durable resume/protection state, live-event overlap reconciliation and validated
activation. Such real control state is justified; the old generation bureaucracy
is not automatically justified. Public versioning remains `public_v*`, with build
identities private.

**Exit check:** the live path imports no old planner/CDC/writer; rebuild tests prove
continued ingestion, restart, overlap completeness, activation and cancellation.
Deleting old code alone does not pass the rebuild gate.

### 6. P2 — Shared contracts keep engine code entangled

`crawl/control/collections/results.py` imports response models and cursor helpers
from old DuckLake history/arrivals/lineage readers and readiness models from the old
materialization module. New writers import shared records and conflicts from
`platform/catalogue`, and exact claims remain in `retention/identities.py` under
lake-specific names and exception handling.

The catalogue package uses lazy exports, so this is **not** evidence that every old
catalogue connection starts on each import. It is a source-ownership problem that
makes deletion and understanding harder.

**Cleanup:** move the few active evidence contracts, response DTOs/cursors and write
conflict types to their actual domain boundaries. Keep concrete ClickHouse storage
implementations. Do not create a generic multi-engine repository framework merely
to preserve old interfaces.

### 7. P2 — New code already has avoidable duplication and inconsistent operations

The new query helpers filter the old catalogue registry down to four relations
(`query/helpers.py`), while ClickHouse DDL separately defines those relations.
Readiness is expressed in both `collections/results.py:_READY` and the public SQL
publication joins. They need not be textually identical, but must share a documented
completion contract and tests so collection readiness cannot drift from public visibility.

The materializer constructs a full `RepositoryIngestor` and calls its preparation
path to verify raw evidence. A small shared verified-raw boundary would express that
need more directly. This should be extracted once, not copied.

Unlike ingestion, the materializer catches every processing exception and NAKs after
30 seconds (`materialization/materializer.py:62`). Invalid input and deterministic
projection failures have no permanent-failure resolution path. The smaller consumer
is a good starting point, but infinite retry is not the finished operator workflow.

**Cleanup:** one authoritative public manifest, one canonical HTML projection, one
explicit completion contract, and consistent error categories/operation identifiers
across both consumers. Retain small role-specific loops rather than introducing a
general pipeline framework. Preserve fail-stop bounds and uncertain-write fencing
when consolidating timers.

### 8. P2 — Builds, tests and instructions still preserve the old system

`docker/periplus/Dockerfile:24` installs ICU, and line 40 imports the old registry and
validates its terms tokenizer. The live first-slice materializer does not produce
that terms projection. This makes an inactive feature part of every core image build.
`PyICU` remains pinned in `pyproject.toml`. Remove it from this runtime after verifying
all intended callers; reintroduce a tokenizer deliberately when search is implemented.

Do **not** remove DuckDB or PyArrow wholesale: `crawl/runtime/selection_sql.py` uses
both for bounded follow execution and `navigation.py` uses Arrow. Their lake usage
can disappear while those concrete callers remain.

`Makefile` catalogue/benchmark commands, `.env.example`, `ducklake.sh`, canonical
architecture/schema/lifecycle instructions and root AGENTS still describe the old
system in significant places. Existing tests include old implementations and fixtures.
Passing them can preserve obsolete contracts while missing the live failures above.

**Cleanup:** remove superseded commands, dependencies, fixtures and packaging entries;
update authoritative docs and contributor rules in the same changes that delete code.
Preserve semantic tests by running them through the replacement, especially lineage,
shared acquisitions, HTML semantics, replay and query isolation. Do not equate a
smaller test count with lost protection when obsolete implementation tests are removed.

## What the boring target should look like

| Owner | Responsibility |
| --- | --- |
| Postgres | Customer intent, permissions/accounting, current frontier, transactional outbox, exact operational transitions, necessary write/protection/rebuild control |
| JetStream | Durable bounded work delivery, redelivery and inspectable failed work; no duplicate authoritative job lifecycle in KV |
| ClickHouse | Immutable crawl evidence and analytical lineage, material outputs, explicit complete-publication state and public query views |
| Raw object repository | Immutable content-addressed bytes and explicit protection/deletion protocol |
| Crawler | Capture/store/freeze/publish, with existing domain pacing and frontier ownership |
| Ingestor | Validate and commit/reconcile base evidence, then ACK |
| Materializer | Verify/read raw evidence, parse/project, publish/reconcile complete output, then ACK |
| Janitor | Bounded named maintenance jobs; no lake file management and no implicit deletion authority |

Keep the current independent ingestion/materialization subscriptions: moving HTML
parsing into database SQL is not necessary to make this architecture simpler.
The consumers may complete in either order; public readiness must still require
matching durable base evidence and complete material output.

## Cleanup sequence and acceptance

1. **Make the running product honest.** Fix metadata and remove or implement dead
   UI/API surfaces; align public docs and helper metadata. Add real route coverage.
2. **Restore safe maintenance and correct errors.** Small janitor, ClickHouse failure
   classification, materialization failure inspection/retry. Keep raw deletion off.
3. **Simplify ingestion ownership.** Remove KV job copies and redundant preparation,
   batching and coordination only after replacing their active receipt callers and
   proving replay/accounting behavior.
4. **Delete the superseded backend.** Extract shared contracts/parser primitives;
   delete old lake planner/CDC/storage/lifecycle code, obsolete models, build hooks,
   configuration and implementation-specific tests. Align Compose, Helm and AGENTS.
5. **Implement remaining capabilities directly.** Background rebuild and safe retention,
   then required public search/JSON-LD/other projections against their business cases.
   These are feature completion, not excuses to retain the old runtime indefinitely.

The cleanup checkpoint passes when a fresh install runs the same E2E and recovery
cases; every exposed product route works; transient data is reclaimed; operators can
trace and resolve failures by identity; no production entrypoint/build depends on the
superseded lake runtime; deployment and documentation describe one architecture; and
each remaining persisted workflow record has a named owner and a necessary invariant.

Full replacement additionally requires EXIT_CRITERIA, including rebuild, retention,
restore and measured homelab performance. This audit establishes neither billion-item
capacity nor production readiness.
