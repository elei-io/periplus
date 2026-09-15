# ClickHouse implementation evidence

## Real capture through base ingestion — 2026-09-14 UTC

The experiment worktree is `codex/clickhouse-experiment`. Local Periplus Compose
volumes were reset with user authorization. Production Periplus storage was not
changed. The existing homelab Stolosio CDP endpoint performed the acquisition.

A normal finite collection was admitted through `FrontierStore.create_collection`
with one seed, `https://example.com/`, a one-page budget and depth zero. The regular
`periplus-worker crawler` and `periplus-worker ingestor` processes performed the
work. No substitute capture loop or direct synthetic visit insert was used for
this result.

- Collection: `75c75f9c-d1b5-4321-a7f4-ab058cdda567`.
- Visit: `2a1385dc-7fdb-40dd-bf26-e7bf8807ce78`.
- Capture: succeeded, HTTP 200, 559 retained HTML bytes.
- Content SHA-256: `7b6cd9a1d881c4a69bf70b9babb5a4a90e38aa6f9933785508a38f665d4d4aab`.
- Raw repository verification against the stored ClickHouse hash and byte count passed.
- Collection settled with one consumed, supplied and ingested page; causal lineage ready.
- Ingestor consumer had zero pending/unacknowledged messages and acknowledged through
  stream sequence 3. The independent materializer consumer still had one pending visit.

This proves capture, immutable raw storage, producer outbox delivery, NATS base
consumption, ClickHouse evidence insertion and frontier receipt reconciliation for
this one successful crawl. It does **not** prove materialization, public query
readiness, capture edge cases, rebuilds, crash recovery or production capacity.
The active end-to-end goal remains incomplete.

## Implementation checks

Targeted checks currently cover:

- 87 frontier tests, including preserved customer records and bounded resumable
  execution cleanup.
- 37 ingestion tests, including receipt and worker-claim behavior.
- Five ClickHouse HTTP transport tests, including no retry after a disconnected insert.
- Two opt-in local ClickHouse/Postgres integration tests for exact replay, conflicting
  identity rejection, full binary hash/nested attempt round-trip and raw verification.

Run the integration checks from the repository root against disposable local services:

```sh
PERIPLUS_TEST_CLICKHOUSE=1 uv run --project packages/periplus python -m unittest discover -s packages/periplus/tests -p test_clickhouse_evidence_integration.py
```

The broader `make check` is being rerun after the ownership and delivery changes;
passing targeted tests is not evidence that all repository checks pass. Runtime
materialization, historical result reads and public queries still need conversion.
The ingestion KV result ledger also remains to be removed as the delivery path is
simplified; it is not part of the intended final architecture.

## Live materialization and native public views — 2026-09-14 UTC

The default materializer process now consumes the retained visit lane directly.
For the real Example Domain visit above, it wrote 12 element spans and one
resolved link, then acknowledged delivery. The canonical document text is stored
once; descendant text is reconstructed with Unicode code-point offsets.

The first physical HTML/link publication consists of a content-owned
`material.html_documents` row and a visit-owned `material.visit_results` row.
Both have deterministic output digests. Each is one bounded insert row. The final
visit row is written only after the content row is visible on the write route;
it contains all of that visit's resolved links. Public capture readiness joins
this row to base evidence by both visit identity and the exact evidence digest,
and requires its referenced HTML content. The public element and link views are
gated by those completed captures. This is currently a single-server protocol,
not a demonstrated replicated publication or background rebuild protocol.

The current public views are `capture`, `html_element`, `link` and `page` in
`public_v1`. Native ClickHouse queries return the real page's `h1` text,
`Example Domain`, and `https://iana.org/domains/example`. They use definer views;
separate query-user privileges and the HTTP query-service conversion remain work
in progress. Metadata, JSON-LD and search capabilities are not claimed complete.

An opt-in integration test proves that a rejected final publication leaves content
hidden, that completed material without base evidence remains hidden, and that
replay plus base ingestion yields exactly one public capture/link. Unicode text
extraction is verified. Two parser tests cover Unicode spans and deeply nested
text without repeated descendant strings.

```sh
PERIPLUS_TEST_CLICKHOUSE=1 uv run --project packages/periplus python -m unittest discover -s packages/periplus/tests -p test_clickhouse_material_integration.py
```

The HTTP query service still uses the old backend. Native public-view results do
not satisfy that remaining goal requirement. Full repository checks currently
need adaptation where they assert the removed collection-history transfer, and
are being rerun after the materialization changes.

## Native query service implementation

`QueryService` now uses the restricted ClickHouse account and native ClickHouse
SQL. Its direct execution of the real capture's heading query returns
`[["Example Domain"]]`. The query account was verified to reject direct reads of
`ingest.visits` and `material.visit_results`, and DDL against public views.
Positional binding preserves source order across CTEs; parameter injection and
private/table-function source rejection have targeted tests. SQL is sent in the
HTTP body to avoid URL-length limits. Results report `source_snapshot: null`;
there is no invented DuckLake-equivalent snapshot number.

The HTTP entrypoint uses this service, but a complete live HTTP execution is not
yet verified. Its authoritative `/access` policy dependency is the control API,
whose startup still opens DuckLake. That startup ownership must be converted;
policy defaults or a mock policy endpoint are not completion evidence. Query
stream cancellation, helper metadata and remaining supported query cases still
need validation under the new service. Repository checks remain in progress.

## Full live HTTP path verified — 2026-09-14 UTC

The control API now starts without a DuckLake attachment. Crawl result readiness,
arrivals, reverse lineage and activity reads use ClickHouse; collection history
comes from retained Postgres customer records. The obsolete lake administration,
physical storage, generation-management and document routes are no longer
registered by this experiment's control API. Replacements beyond this first
HTML/link flow remain part of the broader migration.

`periplus-setup` successfully installed the ingestion, material and public schemas
and restricted query account. Both HTTP services reported healthy. Buffered and
NDJSON queries returned the real first capture's heading and link using
limits fetched from the real control API.

A second, fresh collection was then submitted through `POST /collections` with
reuse disabled:

- Collection: `ff912d12-6189-4057-b95c-be4fb3f3847a`.
- Capture: `19834de2-d0f4-4903-af2d-3909e86d5496`.
- HTTP query: `a1c096f6-d340-463c-9c91-2470e56a6869`.
- Result: capture identity, `Example Domain`, `https://iana.org/domains/example`.
- The collection was observed settled and query-ready within 25 seconds of API
  submission. This is a single idle-system smoke observation, not a percentile
  or capacity benchmark.

The repeatable smoke command uses the actual control and query endpoints:

```sh
uv run --project packages/periplus python scripts/clickhouse_smoke.py
```

It requires the normal API, query, crawler, ingestor and materializer processes,
configured with the same local stores and the authorized Stolosio CDP endpoint.
Full repository checks and Compose conversion remain incomplete, so the goal has
not yet been marked achieved.

The smoke script itself was executed successfully: collection
`e882c1cd-a640-457f-8042-171d4cd3b32e`, capture
`0c24e247-56ea-4d2a-b191-b413fcef982e`, query
`4c3512e0-5aaa-4328-bf3e-ddb75029bfa6`. Query-ready state was observed after
16.55 seconds, and the expected complete heading/link row was returned.

## Compose wiring and replay checks — 2026-09-15

Compose validation now passes with ClickHouse as the backend, separate writer and
query credentials, and no ClickHouse credentials in the crawler. The old lake
metadata, query-access initializer and janitor services are absent from this
local topology. The core image no longer installs storage/CDC extensions.
The image build is in progress; this is not yet container-run E2E evidence.

The four ClickHouse evidence integration tests passed against disposable local
Postgres and ClickHouse. Added cases synchronize two independent consumers and
verify one created receipt plus one replay receipt and exactly one visit row.
A second case performs a real insert then injects a lost response: receipt
reconciliation finds exactly one durable row, and the uncertain writer's exact
claim still rejects another writer. This does not yet test replay after real
claim expiry or process death. Fixture rows and claims are disposable test state.

All four HTTP streaming tests pass with the new service interrupt interface,
including backpressure, disconnect cleanup, admission-slot ownership and terminal
failure handling. Server-side cancellation during an active ClickHouse request
still requires separate verification. Full repository checks remain in progress.


## Container E2E verified

The core image built successfully, setup exited successfully, and all five backend
roles became healthy through Compose. The host-run API/query/materializer and
crawler/ingestor processes were stopped before the container smoke run; their
recorded PIDs were verified absent. No production Periplus stores were changed.

`uv run --project packages/periplus python scripts/clickhouse_smoke.py --query-url http://127.0.0.1:8010`
passed with collection `b7ac79e8-795a-4e65-b8a0-8fc41c79eb1e`, capture
`3c85cfe0-27ed-4533-917e-daf565c898a9`, query
`b48aed4b-bdf2-48be-acbc-19c91ed89a96`. Readiness was observed after 16.88 seconds;
the exact heading and IANA link row matched. Existing local volumes were retained
for this run; this is not yet a second clean-volume reproduction or backup test.

The full check run completed with 790 tests, 4 failures, 53 errors and 38 skips.
Collection API fixture ownership and setup-order tests are being adapted next;
query-service and retired lake contract coverage remains substantial unfinished work.

## Control API contract checks

All 27 collection API tests now pass against the retained-Postgres contract.
They verify immutable intent on replay, admin intent protection, customer visibility
and frozen counts during execution pruning, and degraded readiness without hiding
control state. A disappearing control record returns an error rather than recreating
intent from analytical history. Readiness fixtures use no generation identity.

All eight schedule tests pass. The transaction-failure case now injects failure at
the actual SQLAlchemy commit after collection launch and verifies both the new
collection and schedule execution count roll back. Collection creation is no longer
expected to enqueue analytical copies of editable definitions. The two setup tests
also pass with the new ClickHouse bootstrap order. Full checks were restarted after
these adaptations; remaining query/runtime test conversions are not complete.

## Frontier delivery checks

The four outbox tests and three assembled frontier-runtime tests pass with typed
acquisition/fulfillment lineage. The runtime test observes actual Postgres settlement
and verifies frozen discovery provenance there, while independently checking one
capture, one visit job, one fulfillment and capture ACK. It no longer relies on a
removed collection-outcome job to signal completion. Pending and failed ingestion
states still cannot advance commit receipts.

The lineage suite (17 tests) passes after explicitly testing that editable collection
definitions and outcomes cannot enter ingestion jobs. Its remaining DuckLake storage
fixtures exercise unretired modules and are not ClickHouse persistence evidence.
The last full check completed with 792 tests, 32 errors and 40 skips; it began before
these frontier test adaptations. Query-service contract conversion remains open.

## Materializer outage recovery verified

`scripts/clickhouse_recovery_smoke.py` was executed against local Compose. It stops
the materializer, submits a fresh Stolosio crawl, verifies base ingestion and lineage
completion while public readiness is false, and asserts the public capture query
returns no row. It then starts materialization and verifies the exact heading/link
result for the same capture. The worker restart is protected by `finally`.

Collection `d3646b3e-a8ae-4007-8c1f-dd24669a7e2e`, capture
`26b390da-bc91-4c9b-ac7f-b976fbd12842`, query
`d9b14fc7-acf8-4bcc-bf92-2570b907d89c` passed. Recovery was observed 1.13 seconds
after the Compose start command returned; that excludes container startup time.
This proves delivery retained while a consumer was stopped, not crash-after-commit
redelivery. Compose start also reran the idempotent setup dependency in this run.

Nine native query tests now pass, including parameter validation, preparation,
stream/buffer row equivalence, independent row/byte budgets, admission ownership,
consumer-disconnect cleanup, and no automatic replay on a database failure. Their
execution cases use a mocked database transport and do not prove server-side
cancellation or native SQL semantics; the real HTTP smoke remains separate evidence.

## Query occupancy observability

The native query port had omitted `periplus_query_active_operations`. The service
now increments it only after admission and decrements it in terminal cleanup.
Three adapted tests verify successful and failed execution, busy rejection, and
validation failure without database I/O. All nine native query validation/execution
tests also pass after this runtime change. The running container still needs a
rebuilt image before its metrics can be used as evidence for this revision.

## Clean-volume reproduction verified

After the rebuilt core image completed, `docker compose down --volumes` removed
only this local Periplus project's five named volumes and network. Compose then
created empty ClickHouse, control Postgres, NATS and raw-object stores. Setup exited
successfully and all five backend roles became healthy without host-run services.
The production homelab Periplus stores were untouched; only its authorized Stolosio
CDP service was used for acquisition.

The normal smoke command against query port 8010 passed: collection
`13d54787-abd4-4c6c-ad4d-b6419034f602`, capture
`9a062b92-715d-432f-8adc-08dab09fcd36`, query
`1c54d792-73bf-4ad1-8898-7329c223594d`. Readiness was observed after 16.67 seconds.
The public heading/link row matched exactly. An independent raw repository read
verified SHA-256 `7b6cd9a1d881c4a69bf70b9babb5a4a90e38aa6f9933785508a38f665d4d4aab`,
559 uncompressed bytes and 399 stored bytes against the fresh ClickHouse visit.

The latest full check completed with 796 tests, 28 errors and 40 skips. The clean
E2E establishes repeatable setup and the requested happy path; it does not resolve
remaining query contract tests, crash-after-commit redelivery or active-request
cancellation. Those gates remain open.

## Closed-query lifecycle guard

The native service now rejects prepare/execute calls after close before using the
HTTP transport. The guard runs inside admission cleanup, so neither the slot nor
active-operation gauge leaks. Two added tests cover closed-service rejection and
an already-cancelled request performing no database I/O, followed by successful
service reuse. All 11 native query tests pass. Full checks were started after the
change; live containers have not yet been rebuilt with this guard.

## Native query-account isolation verified

The two opt-in tests in `test_clickhouse_query_integration.py` passed against the
fresh local ClickHouse server using the restricted query credentials. Direct SQL
reads public views but cannot read ingestion/material tables, create a public table,
or raise the configured memory/thread/execution-time limits. This bypasses Periplus
SQL validation and therefore tests the server privileges themselves.

Native prepare, buffered execution and emitted row frames preserve Unicode and a
9007199254740993 integer in Python. This is not a JavaScript JSON precision proof.
The obsolete DuckLake public-query limit test was replaced by this native coverage;
the four remaining DuckDB client-limit tests pass. Broad old QueryService fixtures
and browser/SDK contract coverage remain unconverted.

## Public JSON integer precision restored

Review of the previous public query serializer identified a dropped wire guarantee:
integers outside JavaScript's safe range must be emitted as strings. The ClickHouse
service now converts these values recursively in rows, arrays and objects before
byte accounting and before buffered or streamed output. Booleans and safe integers
keep their types. Twelve native query unit tests and both real ClickHouse query
integration tests pass; the large-integer integration case now expects a string.
The earlier Python-only precision observation is superseded by this wire contract.

## Python SDK native HTTP contract verified

The SDK required a numeric source snapshot in both buffered and streamed models,
which rejected valid ClickHouse responses. Both now accept null. A new integration
test routes the real SDK through FastAPI to a real ClickHouse QueryService and
checks both buffered and streamed results, completion, null snapshot and exact
large-integer strings. All three native query integration tests pass. The SDK's
24-test suite passes with six existing skips. Browser TypeScript contracts remain
to be inspected; this is not a browser compatibility claim.

## Public application snapshot contract

The public application's `QueryResult.source_snapshot` type now accepts null.
Its SQL-exploration test verifies this passes through while retaining sampling
and parameter forwarding. The public application type check, all 51 tests and
production Next.js build passed. Admin query-history already permits null; its
separate old materialization-run snapshot fields were not changed by this query
contract fix. This validates compilation and tested data handling, not a complete
browser interaction or the unported experimental query route.

## Query test ownership converted

The old QueryService suite instantiated DuckLake for every test, causing all cases
to fail before reaching assertions. It has been replaced with native HTTP boundary
coverage for authentication, actual advertised capabilities, safe server-error
mapping, admission cleanup, per-operation limits and metadata-byte rejection.
Native execution/parameter/precision/stream/replay tests live in the ClickHouse
unit and integration suites; SDK HTTP coverage uses a real ClickHouse service.

Removed fixture expectations were tied to the retired experimental optimizer-pass
runner, dual query modes, DuckLake snapshot pinning and connection poisoning.
Search/graph expansion beyond the first four public views is still unported and is
not claimed complete. The legacy search projection tests now inspect their own
catalogue registry, rather than demanding the live ClickHouse endpoint advertise
an uninstalled macro. The already-unregistered DuckLake administrative SQL module
and its script-transaction tests were deleted; no replacement writable HTTP route
is introduced in this first slice. Its old frontend caller remains unported.

## Process-death publication replay

The material publication integration test now runs successful final publication in
a separate Python process and calls `os._exit(23)` immediately after it commits,
before the caller can receive completion. The parent checks the exit status and
replays the identical output. Replay reports an existing result; exactly one
material visit, public capture and link remain. The earlier partial-publication
visibility assertions still pass. This is real process-death storage recovery,
not yet a JetStream ACK/redelivery test.

The core image was rebuilt and the local query container recreated with the latest
serializer and lifecycle guard. A direct HTTP query to port 8010 verified the
large integer is a string and source_snapshot is null on that deployed image.

## Real JetStream crash-after-commit redelivery verified

The material recovery fixture now uses a uniquely named disposable FILE-backed
JetStream stream and explicit-ACK durable consumer. A child process pulls the event,
commits material output, and exits with code 23 before ACK. The parent verifies
JetStream delivers that same event at least twice, reconciles without creating new
output, ACKs synchronously and verifies zero pending and zero ACK-pending messages.
The stream is deleted in finally; application streams/consumers are untouched.
The complete partial-output/publication/replay test passed in 1.90 seconds.

The full `make check` run completed successfully: 782 backend tests (43 skipped),
24 SDK tests (six skipped), shared package checks/tests, public application checks
and build, and admin checks/build. This run preceded the latest opt-in JetStream
fixture edit; that fixture was exercised separately against the real local stores.

## Active HTTP cancellation implementation

The read-only ClickHouse client now opens a fresh connection per request, observes
connection establishment through HTTPcore's documented trace extension, and shuts
down only its active socket on service interruption. Writer clients reject this
operation. Cancellation is reported as a timeout, and the service remains reusable.
The implementation follows https://www.encode.io/httpcore/extensions/ and does not
inspect private pool fields or grant the query account KILL QUERY privileges.

Five client tests and twelve native query tests pass. A live SELECT sleep(3) test
observed the query in system.processes, interrupted it, received client cancellation
immediately, and successfully reused the service. The server entry disappeared
3.005 seconds later, at the end of the sleeping function. This does not establish
immediate server cancellation; native operators may only observe cancellation at
checkpoints. CPU-bound and HTTP disconnect lifecycle checks remain to be added.

## CPU-bound server cancellation verified

A real read-only ClickHouse request hashing ten million numbers was observed active
in system.processes and then interrupted. Its client future terminated with
TimeoutError, its exact server query ID disappeared after 0.038 seconds, and the
same client successfully executed a new query. The reproducible integration test
requires client completion and server disappearance within two seconds after
interruption and also verifies reuse. All four native query integration tests pass.
This complements the sleep test: cancellation is cooperative inside native operators,
while the HTTP lane can terminate immediately. The test uses an isolated query ID
and does not issue server-wide KILL commands.
