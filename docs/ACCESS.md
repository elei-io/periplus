# Public access and shared requests

`/observatory/access` edits three independent public capabilities: crawl submissions,
dataset assistant turns, and direct SQL executions. The control API stores one versioned
policy in Postgres, together with at most three current admission windows. These are
operational controls, not historical admin telemetry.

Each capability has an enabled flag and a global requests-per-window allowance. Postgres
locks the singleton row briefly to make admission atomic across replicas, using database
time. Windows start with their first accepted attempt. At exhaustion the API returns 429
with `Retry-After`. Editing policy preserves consumed capacity; a stale edit returns 409.
Rejected disabled features or unsupported crawl options do not consume capacity. Accepted
attempts consume capacity even if later execution fails. SQL preparation checks availability
but does not consume an execution allowance. The initial limits are 6 crawl submissions,
10 assistant turns, and 60 direct SQL executions per 60 seconds.

Crawl policy also specifies allowed page budgets, depths and retention periods, each with
an allowed default. The API validates submitted choices. An existing request identity can
be replayed with identical intent without a new allowance, even while submissions are
disabled. Admin creation and scheduled executions bypass public admission controls but
retain per-request budgets, domain policies and worker limits. Disabling public access does
not cancel admitted work or ongoing streams.

The public UI fetches `/api/access`, refreshes every five seconds and on focus, disables
unavailable actions and derives crawl choices from the policy. It preserves existing results
and conversations. Backend denials remain authoritative for races; clients refresh policy
and honor rate-limit retry times. Availability failures disable new work until recovered.

The public Next.js gateway checks assistant and direct SQL admission through the control
API using its server-only public service credential. The isolated query service has no new
Postgres dependency. Assistant tool queries use the trusted internal query credential and
are independent of the direct SQL switch. Catalogue metadata and the fixed capture-count
read remain available. The capture-count endpoint accepts no caller SQL. The query service
must remain behind the trusted gateways; its service credentials never reach browsers.

`request_class` is `public`, `system`, or `admin`. The public creation endpoint always assigns
`public`; admin intent defaults to `admin` and may be marked `system`. A schedule copies its
saved definition's class into each frozen execution. Class is descriptive metadata, not an
access boundary. All evidence, lineage and compatible acquisitions are shared. There is no
private request, evidence visibility field, or access-context partition.

This is physical catalogue contract 7.0.0. Control migration 0008 rejects an existing frontier
or saved definitions because the acquisition identity and immutable specification contract
changed. Development cutover requires fresh disposable control, delivery and lake state;
old evidence must not be silently rewritten. Do not run a volume reset against data that
must be retained. No compatibility reads or aliases are provided.

Validation for this cutover passed `make check`, a fresh Postgres upgrade through 0008,
and an eight-caller Postgres race with exactly three admissions for a three-request limit.
Browser checks verified saved toggles, public policy polling, expanded crawl choices, readable
retention durations, and SQL denial before query execution. An isolated live depth-zero crawl
of example.com produced one physical observation shared by public, system and admin requests,
with three durable fulfillments. Existing local data was not reset by these checks.

The authorized local cutover completed on 2026-09-08: all five Compose data volumes were
reset, current images rebuilt, migration 0008 and catalogue 7.0.0 installed, and every service
reported healthy. A one-page admin request passed capture, ingestion, complete materialization,
public request visibility and public SQL checks. The fresh lake retains that one smoke-test
observation. Catalogue validation and the admin dev route on port 5173 passed; crawl,
assistant and SQL public capabilities were restored to enabled.

## Public coverage queue threshold

The admin public-access page exposes `crawl.queue_limit`: pause new public coverage
requests when queued plus retrying acquisitions reach this number. The default is
10,000; null means unlimited. Dispatched acquisitions do not count. This is a public
submission threshold, not a hard queue bound: already accepted requests keep discovering
links and may grow the queue beyond it. Admin requests and schedules bypass this gate.

`GET /access` includes live `crawl_admission.pending_acquisitions` and `accepting`.
This status is calculated from current queue state; it is not stored in the policy.
At or above the threshold, new public creation returns HTTP 429, code `crawl_queue_full`,
and Retry-After 5 without consuming a public rate-window unit. It reopens automatically
below the threshold. Existing request IDs can still be retried idempotently. The public
UI polls availability and explains the temporary pause; the API enforces it independently
of the UI. Public requests-per-window limits remain separate.
