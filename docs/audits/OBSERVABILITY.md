# Logging and metrics audit

Date: 2026-09-08. Static review of current application source, runtime composition,
Docker/nginx configuration and Helm templates. No production logs, credentials,
Grafana configuration or deployed scrape targets were inspected. No runtime
instrumentation or additional persistence was introduced. Public access controls
are excluded (separate work).

## Overall conclusion

Grafana should own historical operational views and alerts. Prometheus should
receive numeric, bounded-label measurements; Loki should receive sanitized,
structured diagnostic events. Admin owns inspection and deliberate control.
Existing worker metrics provide a useful foundation, but public/query visibility,
log privacy, measurement semantics and health boundaries need work before preview.

## Prioritized findings

### High: full public SQL and parameters are logged at INFO

`packages/periplus/src/periplus/query/service.py:129` serializes the complete
QueryRequest into query_submitted. Both prep and exec take this path. SQL literals
and parameters can contain private research, personal information, tokens pasted
into queries, or sensitive URLs. This is confirmed payload logging, not evidence
that an actual credential has already leaked.

Replace payload logging with operation, query ID, bounded outcome/error code,
duration, truncation and response size. Do not log parameters, SQL literals, result
rows or plans by default. If SQL diagnostics are ever needed, make their handling
explicit and separate; hashing alone does not make predictable sensitive input safe.

### High: raw exception chains can disclose input and infrastructure details

Examples: `ingestion/consumer.py:256`, `crawl/runtime/frontier_capture.py:38`,
`platform/catalogue/operations.py:133`, and materialization retry handlers.
Pydantic validation tracebacks can include rejected input; database/network errors
can include statements, bound parameters, object paths or connection details.
Broad logging.exception / exc_info=True therefore undermines otherwise sanitized
HTTP responses. Exact emitted contents depend on the failing library and input.

Use stable event and error codes with operation IDs and safe diagnostic fields.
Handle validation errors without serializing input or the original exception chain.
Test log output with sentinel passwords, signed URLs, SQL parameters and malformed
job envelopes. Do not treat all stack frames as sensitive, but explicitly control
exception messages and chained causes.

### High: public assistant and query service lack adequate operational telemetry

`periplus-public/src/app/api/assistant/route.ts` catches setup and streaming errors
without a diagnostic event. Its process-local active counter is not exported.
Streaming errors may happen after an HTTP success, so ingress status counts alone
cannot establish assistant success. `src/server/query-proxy.ts` also collapses
transport failures into an unlogged generic response.

The query process (`entrypoints/query.py`) exposes health but no Prometheus route;
its completion logs omit bounded error category and response/truncation data.
There is no application-wide HTTP request instrumentation in the inspected source.

Add attempt/admission/completion/rejection/cancellation counters and latency
histograms, separate for assistant and SQL. Export active slots, safe error codes,
provider token usage when supplied, query truncation and timeouts. Record terminal
stream outcomes explicitly. Distinguish SQL prep from exec and assistant-issued
queries from interactive SQL where a trusted caller context is available. Do not
infer unique people or cost from request counts.

### High: dependency readiness is used as worker liveness

`platform/health.py:73` makes health fail on dependencies, subsystem failures and
queue stalls as well as heartbeat loss. `charts/periplus/templates/workers.yaml:89`
uses the same /healthz endpoint for readiness and liveness. A dependency outage can
therefore cause worker restarts despite a responsive process, amplify recovery
noise and erase process-local telemetry. This is a configured behavior risk, not a
claim that restart loops were observed in production.

Separate process/event-loop liveness from readiness and progress alerts. Keep
queue stalls actionable without treating every external outage as a dead process.
The public /api/healthz is process-only; it is not an end-to-end SQL/assistant check.

### Medium: replay can inflate materialization committed-output counters

`materialization/metrics.py:69` increments source/output counters regardless of
already_applied. `materialization/batch.py:210` and its applied-marker result reader
return nonzero historical counts on replay; `materialization/runtime.py:381,483`
forward these to metrics.batch. The outcome counter distinguishes redelivery, but
row/byte totals do not. Thus “committed” output rates can overcount duplicate work.

Separate executed/prepared work from newly committed output; count new committed
rows and bytes only for new commits. Add a replay-specific test. Metrics remain
best-effort process telemetry and cannot replace durable accounting across crashes.

### Medium: some crawler instrumentation is disconnected

`crawl/metrics.py` and `crawl/navigation_metrics.py` declare metric families, but
repository search found no production callers/imports of their recording helpers.
Their existence does not establish measured crawl queue or navigation activity.

Delete obsolete declarations or instrument the current frontier paths deliberately.
Needed signals include physical attempt outcomes/duration, throttle responses,
pacing waits, permit contention, uncertain attempts, dispatch/admission blocks,
and schedule execution lateness/failures. Use bounded reason/outcome labels, not
URLs, domains, execution IDs or request IDs as general Prometheus labels.

### Medium: histogram buckets are unsuitable for several measurements

`ingestion/metrics.py` raw bytes and batch item histograms,
`materialization/metrics.py` phase durations and `crawl/navigation_metrics.py`
row/byte histograms all use Prometheus Python default buckets (seconds-oriented,
ending at 10 before +Inf). Byte and row observations commonly exceed every finite
bucket; long commits also lose useful resolution. Counts/sums remain usable but
quantile panels would be misleading or uninformative.

Choose explicit byte/item/duration buckets from operational bounds and expected
scales. Keep units clear and avoid adding distributions with no intended use.

### Medium: replicated gauges and progress gauges need explicit semantics

Each ingestor/materializer can expose the same shared consumer queue counts.
Summing across replicas multiplies backlog. Use an authoritative NATS exporter or
explicit deduplication (e.g. max for identical consumer observations), with freshness.

`ingestion/metrics.py` oldest_pending_age measures time without observed progress,
not oldest-message age. `crawl/metrics.py` describes time since process observation.
Rename these to no-progress duration if retained. `materialization/metrics.py`
rebuild progress is last locally observed progress, updated by batch completion;
it is not continuously reconciled fleet progress or an active-generation check.
Zero may mean unobserved/no total, and old values can persist. Avoid summing it or
presenting it as authoritative current rebuild completion.

### Medium: scrape success does not establish useful application health

Worker metrics listeners can stay up while the event loop or a dependency fails.
HealthMonitor subsystem/readiness details are not exported as metric families.
Prometheus `up` only demonstrates scrape reachability. Pair this with readiness,
progress timestamps and an external public SQL probe. Monitor query/public/API,
NATS, both PostgreSQL authorities, object storage and CDP separately.

Helm provides worker metrics services/annotations, but deployed target discovery
was not verified. Ensure every replica is scraped individually, not through a
load-balanced service address that merges different process counters. No Grafana
dashboards, Prometheus recording/alert rules or Loki pipeline configuration were
found in the maintained repository; they may live in the external platform.

### Medium: retry noise and log structure impede diagnosis

Workers use timestamped plain text; API/query basicConfig differs. Many call sites
use the root logger, losing module identity. Query logs embed JSON inside text;
exception traces span lines. Expected transaction conflicts emit full warning
tracebacks on each retry (`platform/catalogue/operations.py:133`) and can then be
logged again by the caller. Materialization logs successful batch summaries while
also counting metrics; that is useful correlation, not inherently redundant.

Standardize one structured event schema: event, level, service, operation,
correlation ID, outcome, safe error code, duration and retry attempt. Report first
failure, material changes, exhaustion and recovery; count intervening retries.
Use full diagnostic traces selectively after sanitization. Put high-cardinality
correlation IDs in log fields, not Loki stream labels.

### Medium: access logs and health details need their own privacy policy

Admin nginx configuration does not override access logging. Default request-line
logging can include /?sql= console links; browser request query strings and referers
are not a safe logging surface. FastAPI/Uvicorn access-log configuration is also
not explicitly sanitized. Verify actual runtime formats before asserting what
has been collected. Prefer route/path without query strings; restrict retention
and access for network identifiers.

HealthMonitor returns stored dependency/subsystem error text verbatim. Callers
sometimes pass native exception text. Although these endpoints should be private,
probe diagnostics and collectors can copy them into logs. Use safe reason codes.

### Medium: janitor and privileged actions have little telemetry

Janitor sweeps have informational/error logs but no dedicated reclamation outcome,
duration, last-success or backlog metrics in the reviewed code. Alerting on missing
progress is therefore difficult. Existing storage dashboard data is not a historical
metrics exporter.

Admin SQL (`query/admin.py`) lacks a purpose-built completion audit event. Capture
operation ID, service actor, time, result/error category and duration without SQL
payloads. The current admin service credential is not a human identity: do not
pretend it attributes an edit to a particular person. Apply the same distinction
to policy and schedule mutations. No new audit table is needed for log events.

## What is already good

- Ingestion has useful queue, lane operation, last commit and recovery metrics.
- Materialization records phase timings, conflicts and batch outcomes.
- Most metric labels are bounded phase/outcome/lane values, not user URLs/IDs.
- PostgreSQL checkout count and hold-time instrumentation is useful and bounded.
- Query HTTP errors use safe categories and exception class names, not native text.
- Worker health HTTP access logging is suppressed, avoiding probe chatter.
- Existing logs often contain operation IDs useful for cross-service investigation.

## Recommended order

1. Remove SQL/parameter payload logging; define and test safe exception and access-log handling.
2. Separate worker liveness/readiness; add assistant/query terminal outcome visibility.
3. Fix replay counters, histogram buckets, stale/replicated gauge semantics and dead instrumentation.
4. Add missing crawl/pacing, scheduler, janitor and administrative-action events/metrics.
5. Validate external scrape/log collection, create dashboards and test alerts with controlled failures.

Suggested dashboard groups: public experience; crawler and schedules; ingestion and
materialization; storage/retention; infrastructure. Initial alerts: sustained user
failures, saturation/rejection, backlog with no progress, missing ready workers,
repeated throttling, failed scheduled runs, janitor inactivity and disk capacity.
Thresholds need observed baselines; this audit does not invent production values.

This plan adds only operational telemetry when separately implemented. It does not
propose new application tables, cursors, queues, analytics ledgers or admin histories.

## Implementation follow-up

The findings above describe the pre-fix snapshot. Runtime fixes, regression tests,
a Grafana dashboard, initial Prometheus alert rules and collection instructions
are now in the working tree. See `observability/README.md` for current semantics
and the required platform integration. No deployment or notification routing was
performed by this source change. External exporters, actual ingress logs, collector
credentials, retention and contact points remain platform-owned acceptance work.

Validation: sentinel log tests, HTTP route-label privacy, liveness versus readiness,
materialization replay/superseded accounting, and public terminal-event idempotence
have regression coverage. Promtool validates the alert rules, dashboard expressions
and public metrics exposition; rule tests exercise readiness alert firing and healthy suppression.
External monitoring deployment is not claimed as verified.

Final working-tree checks: observability Python regressions pass (6 telemetry and
5 health tests); public telemetry regression passes. The full Python suite is
currently blocked by 13 collection-test errors from a missing `public_access`
fixture table. Concurrent frontend changes also block checks: the public collection
submission test still expects removed `visibility`. The admin production build
passes. The outstanding failures are outside this observability change.
