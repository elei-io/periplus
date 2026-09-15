# Working in Periplus

Read `docs/ARCHITECTURE.md`, `docs/SCHEMA.md` and `docs/LIFECYCLE.md` before
changing persistence, crawling or worker ownership. Read `docs/DEPLOYMENT.md`
before changing topology/environment contracts, `docs/REBUILDS.md` before
changing materialization, and `docs/CUTOFF.md` before adding an abstraction or
persistence path. Query work follows `docs/QUERY.md` and `docs/QUERY_OPTIMIZATION.md`.

Periplus is greenfield. Change contracts directly and remove superseded paths;
no compatibility shims, dual writes or migration bridges. Keep homelab production
untouched unless explicitly authorized. Local crawling uses the configured
standard CDP endpoint; do not replace it with a local browser implicitly.

## Authorities

- Postgres owns customer intent, collection results, policies, current frontier,
  transactional outbox, import/rebuild controls, publication and retirement work.
- Raw storage owns immutable capture facts, payload bytes, journal events,
  tombstones, recovery manifests and preserved software. The corpus must rebuild
  without the original Postgres, NATS or ClickHouse contents.
- NATS owns delivery, expiring operation leases, crawler presence and domain permits.
  Reconcile contracts at startup; never provision them from request/polling paths.
- ClickHouse owns only disposable material corpus and versioned public views.
  No business records, acquisition reasons or fulfillment mirrors live there.
- DuckDB is bounded page-local navigation/follow SQL only. No DuckLake/CDC runtime.

Keep one shared frontier and one CDP acquisition primitive. Preserve collection-local
deduplication, independent budgets and result associations when acquisitions are
shared or reused. Capture never waits for materialization. Domain politeness is
separate from operation ownership. Never hold a Postgres transaction across
object-store or ClickHouse I/O. Exact claims and bounded workers protect uncertain
writes; do not shorten their drain without a proof.

## Code map

`crawl/control/` owns intent/policies; `crawl/runtime/` owns frontier execution;
`crawl/acquisition/` acquires one page; `ingestion/` owns archive/contracts/imports;
`materialization/` owns derived output and rebuilds; `query/` owns corpus SQL;
`retention/` owns exact claims/tombstone work; `operations/` owns the janitor/admin;
`platform/` contains adapters; `entrypoints/` only composes processes.

Use typed Pydantic boundaries and SQLAlchemy 2 models. Manage control schema changes
with Alembic. Object keys are repository-relative; never expose local paths.
Do not commit generated artifacts, raw local data, virtual environments or secrets.

## Workflow

Use `uv` from `packages/periplus/` (Python 3.14). Run `make check` after Python
changes, with targeted behavioral tests. The isolated archive-only recovery drill
is `scripts/archive_recovery_smoke.py`. For crawl changes, exercise one low-depth
URL at low concurrency. Read package-local frontend `AGENTS.md` before editing;
run both frontend typechecks/builds, plus admin lint/tests when admin changes.

Frontend code uses shadcn, React Query, shared API types and named exports except
`App.tsx`. Every mutation surfaces `toast.error(extractApiError(error))`.
