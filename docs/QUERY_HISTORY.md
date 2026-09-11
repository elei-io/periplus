# Private query history

`observatory/queries` answers which query patterns are common, which fail, and which
consume execution time. It defaults to seven days of public console, assistant and
SDK-attributed executions. Preparation, admin, internal and unknown sources are
separately filterable. Counts are recorded operations, never visitors.

## Ownership and storage

One `query_executions` table in Periplus control Postgres stores terminal execution
metadata, original SQL and bound parameters, normalized SQL/fingerprint, structural
features, safe error codes, result sizes, snapshot and deployment version when known.
There is no capture table, result storage, stored aggregate or extra queue/service.
This deliberately extends control Postgres ownership to private product analytics;
it does not put crawl history in Postgres or add anything to the public lake catalogue.
Migration `20260908_0009` creates the table and time/fingerprint indexes.

The public query HTTP adapter records successful preparation/execution, SQL failures,
timeouts and busy rejections. The admin SQL adapter records its separate writable
operations through the same history contract. Authentication failures, invalid JSON,
oversized bodies, public gateway access denials, helpers and direct DuckDB/lake
connections are outside this history. A malformed SQL string inside a valid query
request is retained even if normalization fails, grouped as explicitly unparsed SQL.

The isolated query service posts to `PERIPLUS_API_URL/internal/query-history` with its
existing query-service token. API recognizes this token for that bounded append endpoint and `GET /access` public settings
reads; it grants no history reads or control mutations. API and admin use the normal
repository boundary for Postgres. Query receives no control database, NATS, or lake
writer credentials. Source attribution is asserted by authenticated service clients,
not a user identity/security boundary. Public proxy permits the caller-asserted `sdk` label and
maps all other input to `public_console`; assistant and crawler clients set their own source.
The public Python SDK sends `x-periplus-query-source: sdk` through that gateway. Direct internal
HTTP clients can send the same header; clients that omit it are `unknown`.

Delivery is best-effort, with eight bounded in-process recording slots, no retry queue,
and a one-second remote delivery deadline. History failure does not change a query's
result. Duplicate execution IDs are ignored, never double counted. Crashes/cancellation,
backpressure, oversized derived records or storage outages can lose history. Watch
`periplus_query_history_delivery_total{outcome="failed"|"dropped"}` in query/API metrics.
The narrow endpoint caps payloads at 1 MiB. Terminal writes have a one-second Postgres
statement timeout. No history payload is emitted to operational logs.

## Measurement semantics

Elapsed time covers the server operation through result construction and transaction
cleanup, excluding history normalization/delivery. Streaming execution includes producer backpressure;
buffered execution excludes response transport. Result bytes
are the UTF-8 size of the JSON rows array, not wire/compressed bytes, scanned bytes or
CPU cost. Preparation has no result-size or snapshot measurements. All unavailable
values are null. Version is supplied by `PERIPLUS_SERVICE_VERSION` (Helm uses core image
tag); unconfigured local versions are unknown. Admin SQL has no source snapshot.

p50/p95 use successful operations only and expose their sample counts. Failure rate
includes every non-success outcome; failure categories and recent executions retain
timeouts/rejections separately. Total time includes all outcomes. Daily buckets use
UTC and can be partial at the ends of a rolling window. Patterns group SQLGlot-derived
DuckDB templates; the fingerprint includes the normalizer version. Literals, bound
parameter references and comments are removed from templates. Identifiers and query
structure remain, so templates are also private. CTE aliases are not physical relations.
SQLGlot analysis is descriptive and never rewrites the executed SQL or authorizes it.

All dashboard aggregation runs directly in Postgres over at most 30 days, with five-second
statement timeouts and bounded, paginated result lists. Raw SQL/parameters load only in
the execution detail, whose responses are `Cache-Control: no-store`.

## Retention and rollout

The existing janitor always removes rows older than 30 days, independently of lake
retention mode, in bounded batches of 5,000 per sweep. Reads exclude expired rows even
if cleanup lags. Alert on janitor query-history phase failures. Physical deletion can
lag the cutoff by the janitor interval or backlog; backups follow platform retention.
No separate capture toggle or expiry exists. No historical backfill is possible.

Apply migrations through normal setup, then deploy API, query, janitor and frontends.
The control API now also needs `PERIPLUS_QUERY_API_TOKEN`; query needs the private API
URL. Compose and Helm wire these explicitly. Do not reset existing development state
to bypass an earlier migration's cutover guard: the shared-request migration has its
own coordinated rollout requirements. Production operator ingress must protect all
history reads and raw SQL details. The history token is not an admin token.

Focused tests: `uv run python -m unittest discover -s tests -p test_query_history.py`.
Set `QUERY_HISTORY_TEST_DATABASE_URL` to an isolated disposable Postgres database to
also test real percentile queries, deduplication and cleanup. Those tests create/drop
a unique test schema and must not point at an application database.

## Verification

`make check` passes, including 561 backend tests (32 environment-dependent skips),
23 SDK tests, shared packages, and both frontends' typecheck/lint/tests/builds.
The eight focused history tests also pass with isolated Postgres enabled. The full
migration chain was exercised on a fresh database; revision 0009 was then applied
to the existing local revision 0008 without resetting data. Helm and Compose render,
and Promtool validates the ten alert rules.

Browser verification covered populated pattern statistics and exact SQL/parameter
drill-down against disposable fixtures. The local API/query/janitor/public/admin
containers were rebuilt and restarted. Public and admin read-only smoke queries
both produced history records; their SQL markers were absent from API/query logs.
The two smoke-test records remain subject to ordinary 30-day retention. Production
has not been deployed and external monitoring rule installation remains platform-owned.

## Preparation plans

Revision `20260908_0011` adds nullable preparation evidence to the same retained row:
64,000-byte plan previews, explicit preview truncation, preparation diagnostics,
DuckDB and compiler contract versions, and effective duration/row/result-byte limits.
Capture occurs before execution so a subsequent SQL failure or timeout retains the
available evidence. Early failures and old records have null fields. SHOW output is
not stored as a plan; privileged admin scripts do not generate preparation plans.
No additional EXPLAIN, execution or profiling pass is introduced.

The preview explains the submitted SQL before the existing service row-limit wrapper.
It contains estimated operators and cardinalities, not actual operator timing or work.
`plan_fingerprint` hashes the complete preview with DuckDB and compiler versions;
truncated previews have no fingerprint. This is exact preview identity, not a
parameter-independent structural fingerprint. Literal values, cardinality estimates,
source changes and formatting can all change it. Compare SQL parameters, snapshots,
versions and effective limits before attributing latency changes to the optimizer.
Bump the compiler contract version when changing preparation/validation semantics.

A selected SQL pattern shows at most the 50 most recently seen plan/version groups,
with successful p50/p95 sample counts, timeout counts/rates, first/last seen and a
representative execution link. Missing/truncated plans remain an explicit ungrouped
category per engine/compiler version. No plans or parameters are returned by aggregate
or execution-list reads: they load only in the private execution detail's Plan tab.
Plans can contain private literals and share SQL's access controls and 30-day deletion.
Apply the migration before deploying API, query and admin; historical plans are not
backfilled and unavailable evidence is not inferred.


Notebook streams record their final delivered row count and JSON row-array bytes without
retaining result rows. Large notebook input parameters remain subject to the existing 1 MiB
history-record ceiling; oversized derived records can be dropped without failing the query.
Input budgets do not enlarge private history storage or add another persistence path.
