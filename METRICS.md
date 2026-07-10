# Atlas Operational Metrics

This document records the intended observability model and developer experience for Atlas operational metrics. It is an implementation guide, not a commitment to build an in-app dashboard. Atlas should expose a Prometheus scrape surface; Prometheus and its downstream tooling own storage, querying, dashboards, and alerting.

Atlas already has `backend/metrics/` for historical, Prometheus-shaped JSON used by UI/API surfaces. That is distinct from the live operational metrics described here. Live instrumentation will live under `backend/observability/`, with call sites importing semantic helpers such as `from observability import crawl_metrics`. The existing `backend/metrics/` package remains responsible for historical UI/API data. Both packages must be included explicitly in the Python package configuration.

## Goals

Metrics should answer four questions:

1. Is work flowing?
2. Where is it waiting?
3. Is it succeeding and producing useful output?
4. What capacity or dependency is constraining it?

The most important latency decomposition is:

```text
task queue wait
  -> crawl/browser capacity wait
  -> page acquisition
  -> extraction and persistence
  -> task completion
```

End-to-end duration remains useful, but it must not be the only duration. Operators need to distinguish an undersized worker pool, global browser saturation, one saturated CrawlPolicy, a slow domain, and slow downstream processing.

Metrics are not the source of truth for an individual run. Prometheus answers aggregate operational questions. Postgres holds durable task/crawl state, logs explain individual failures, and progress events support the live user experience.

## Metric design rules

- Use counters for events and outcomes.
- Use gauges for current state or capacity.
- Use histograms for latency and size distributions.
- Prefer rates and latency distributions over averages.
- Record durations in seconds and sizes in bytes.
- Use the `atlas_` namespace.
- Follow Prometheus conventions: counters end in `_total`, durations in `_seconds`, and sizes in `_bytes`.
- Define all instruments, label names, accepted label values, help text, and histogram buckets centrally.
- A metric recording failure must never fail or delay user work.
- Metrics transport is lossy by design. Durable business state is not.

### Cardinality policy

Labels must come from small, bounded sets. Suitable labels include:

- `primitive`
- `status` or `outcome`
- `trigger_kind`
- `mode`
- `source` (`network` or `cache`)
- `scope` (`browser`, `policy`, or `unknown` where loss attribution is impossible)
- `blocked_scope` (`none`, `browser`, `policy`, or `unknown`)
- normalized `reason`
- HTTP `status_class`
- a bounded CrawlPolicy metric slug or configured domain group

Do not use the following as general metric labels:

- URL or raw hostname
- task, run, crawl, artifact, or lease ID
- exception text
- progress operation ID
- user prompt or other input data

Raw host reporting is allowed only through an explicit, bounded allowlist. Full per-domain investigation belongs in Postgres or logs. If broad domain reporting becomes necessary, use a periodic bounded top-N aggregation rather than creating a series for every host.

CrawlPolicy capacity is the one place where policy identity is inherently required. Before capacity metrics are added, CrawlPolicy must gain a required, unique, immutable, operator-facing `metric_slug` with a restricted character set and length. Creation and migration may generate an initial readable slug from policy configuration plus a collision-resistant suffix, after which changing it requires deliberate operator action and creates a documented time-series discontinuity. Policy capacity metrics use that slug. Crawl latency and outcome metrics use an optional, explicitly configured `domain_group`; policies without one use the fixed value `unclassified`. Never derive either label from a URL, raw hostname, match expression, prompt, or UUID at recording time.

Core label vocabularies are fixed initially:

- Crawl mode: `static`, `dynamic`, `app`
- Page source: `network`, `cache`
- Page outcome: `succeeded`, `failed`, `cancelled`
- Task terminal status: `succeeded`, `failed`, `cancelled`, `skipped`
- Task attempt outcome: `succeeded`, `failed`, `cancelled`, `interrupted`
- Permit outcome: `acquired`, `timeout`, `cancelled`, `lease_lost`
- HTTP status class: `1xx`, `2xx`, `3xx`, `4xx`, `5xx`, `none`

The central catalog may define additional bounded vocabularies. Unknown external conditions map to an explicit `unknown` value; arbitrary values must not pass through.

## Initial metric families

Names and initial bucket boundaries below are the intended public Prometheus contract. Change them cautiously after observing real workloads.

### Worker and task orchestration

- `atlas_workers_live`
- `atlas_worker_capacity`
- `atlas_worker_active_runs`
- `atlas_workers_stale`
- `atlas_oldest_live_worker_heartbeat_age_seconds`
- `atlas_task_runs_queued{primitive}`
- `atlas_task_runs_running{primitive}`
- `atlas_task_run_oldest_queued_age_seconds`
- `atlas_task_run_queue_duration_seconds{primitive}`
- `atlas_task_run_execution_duration_seconds{primitive,status}`
- `atlas_task_runs_total{primitive,status,trigger_kind}`
- `atlas_task_run_attempts_total{primitive,outcome}`
- `atlas_task_run_recoveries_total{reason}`
- `atlas_task_run_cancellations_total{phase}`

Normalized recovery reasons should initially include `lease_expired`, `worker_exit`, and `execution_timeout`.

Queue depth must always be accompanied by oldest queued age and a queue-duration histogram. Queue depth alone cannot distinguish healthy bursts from stuck work.

Worker gauges are cluster aggregates and do not carry a `worker_id` label. Atlas's default worker IDs contain PIDs. A worker is live when `stopping` is false and `last_seen_at` is newer than a configurable staleness threshold; the initial threshold is two heartbeat intervals. Capacity and active-run totals include live workers only. `atlas_workers_stale` counts non-stopping rows older than that threshold but newer than the reporting retention, which defaults to 24 hours through `ATLAS_WORKER_HEARTBEAT_RETENTION_SECONDS`. Workers delete heartbeat rows older than that retention on an hourly maintenance cadence controlled by `ATLAS_WORKER_HEARTBEAT_CLEANUP_INTERVAL_SECONDS`, bounding the table without removing live state. The oldest-heartbeat gauge is the maximum age among live workers, or zero when no worker is live.

Operators can immediately remove stale and gracefully stopped heartbeat rows with `atlas purge workers`. The CLI calls the reusable `POST /operations/purge/workers` API; it never deletes workers whose heartbeat is still live.

Worker capacity is supervisor-owned. Isolated run children renew only their task-run lease and never refresh the worker heartbeat, preventing an orphaned child from keeping a dead worker live or advertising unused slots. Workers scan for orphaned Atlas Playwright task trees every `ATLAS_PLAYWRIGHT_CLEANUP_INTERVAL_SECONDS` (default 300 seconds). When no Playwright driver is active, cleanup also removes detached Chromium profile trees left by interrupted runs; live browsers are never selected. `atlas purge playwright` runs the same node-local cleanup on demand, and `--dry-run` reports what would be terminated.

### Browser and CrawlPolicy capacity

- `atlas_crawl_permits_in_use{scope,policy}`
- `atlas_crawl_permit_capacity{scope,policy}`
- `atlas_crawl_permit_waiters{scope,policy}`
- `atlas_crawl_permit_wait_duration_seconds{blocked_scope,policy,outcome}`
- `atlas_crawl_permit_timeouts_total{blocked_scope,policy}`
- `atlas_crawl_permit_lease_losses_total{scope,policy}`

Prometheus collectors have a fixed label schema. For global browser scope, `policy` is always the empty string; for policy scope it is the policy's `metric_slug`. We should avoid exporting a separately maintained utilization gauge because utilization is derived safely in PromQL:

```promql
sum(atlas_crawl_permits_in_use{scope="browser"})
/
sum(atlas_crawl_permit_capacity{scope="browser"})
```

The current permit acquisition attempts the global browser permit and optional policy permit as one unit, while progress reports only generic crawl-capacity waiting. Instrumentation must preserve which `_try_acquire` call blocked acquisition. Record one wait observation for every acquisition request, including immediately successful acquisitions, not one event per 250 ms polling iteration. The observation begins immediately before the first acquisition attempt and ends when all permits are acquired or the request terminates. It contains the last scope that prevented acquisition and the outcome (`acquired`, `timeout`, `cancelled`, or `lease_lost`). Immediately successful requests use `blocked_scope="none"`; otherwise it is `browser`, `policy`, or `unknown`. The `policy` label identifies the requested policy even when another scope blocked, and is empty when no policy was requested. The current-waiters gauge changes only after a request has actually failed an acquisition attempt.

Permit usage counts only rows whose lease has not expired at collection time. Permit capacity may legitimately be lower than current usage immediately after an operator reduces a limit; showing utilization above 100% during that drain period is correct.

### Page acquisition, crawl outcomes, and latency

- `atlas_page_acquisitions_total{outcome,mode,source,domain_group}`
- `atlas_page_acquisition_duration_seconds{domain_group,mode,outcome,source}`
- `atlas_crawl_navigation_duration_seconds{domain_group,mode,outcome}`
- `atlas_crawls_persisted_total{outcome,mode,domain_group}`
- `atlas_crawl_http_responses_total{status_class,domain_group}`
- `atlas_crawl_failures_total{reason,domain_group,mode}`
- `atlas_crawl_retries_total{reason,domain_group}`
- `atlas_crawl_redirects_total{domain_group}`
- `atlas_crawl_response_size_bytes{domain_group}`
- `atlas_crawl_warnings_total{kind,domain_group}`

`source` distinguishes `network` from `cache`. A page acquisition is one logical request handled by `crawl_one_for_task`, whether it reuses an existing Crawl or creates a new one. Its duration starts after capacity is acquired and includes browser startup, cache normalization or network loading, quality checks, and browser teardown; it excludes capacity waiting and result persistence. This makes the cost of cached pages visible even though cached `CrawlPage.duration_seconds` is currently zero.

Navigation duration is the narrower `_crawl_url`/Crawl4AI network operation and is recorded only for `source="network"`. A persisted crawl is a newly created durable `Crawl` row; cache reuse must not increment `atlas_crawls_persisted_total`. HTTP response totals describe the logical acquisition result, including the stored status returned by cache reuse.

Failure reasons must be normalized rather than copied from exception messages. The initial taxonomy should cover:

- `dns`
- `connect_timeout`
- `read_timeout`
- `tls`
- `http_4xx`
- `http_429`
- `http_5xx`
- `navigation`
- `browser_crash`
- `content_blocked`
- `robots_or_policy`
- `capacity_timeout`
- `capacity_lease_lost`
- `task_lease_lost`
- `cancelled`
- `persistence`
- `unknown`

Classification should live in one shared function and be unit tested. Preserve the raw error in logs and durable crawl records.

### Initial histogram buckets

Buckets are part of a metric's public contract and must be declared centrally. Start with deliberately broad boundaries aligned with Atlas's existing permit and task timeouts:

- Queue and capacity wait seconds: `0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300`
- Page acquisition and navigation seconds: `0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600`
- Task execution seconds: `0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600`
- Response size bytes: `1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864`

Prometheus supplies the `+Inf` bucket. Bucket changes create a schema discontinuity and should be made deliberately after examining real distributions.

### Useful work, frontier, and cache

Page-load success is not necessarily scraping success. After the core metrics are in place, add bounded metrics for:

- URLs discovered, suppressed as duplicates, completed, and truncated by depth/page limits
- frontier pending/in-flight work and frontier age
- extraction records produced and empty extraction outcomes
- cache hit, miss, rejection, age, and invalidation reason
- artifact bytes read/written and persistence failures
- per-run crawl worker utilization

These should reveal whether capacity is idle because the frontier cannot feed it and whether successful crawls produce useful data.

### Dependencies and process resources

Eventually expose Postgres latency/pool pressure, NATS publish latency/failures, progress delivery failures, artifact persistence failures, provider latency/rate limits, and standard process CPU/RSS/file-descriptor metrics. Browser concurrency can be below its configured ceiling while memory or file descriptors are already exhausted.

The API can remain healthy while no worker can claim work. HTTP uptime is therefore not a sufficient Atlas health signal.

## Recording developer experience

Application code should import Atlas semantic helpers, not `prometheus_client`. Call sites should not choose metric names, buckets, registry behavior, or subprocess transport.

The preferred form is a domain context manager that records active work, duration, outcome, and failure classification together:

```python
from observability import crawl_metrics

with crawl_metrics.page_acquisition(
    domain_group=domain_group,
    mode=mode,
    source="network",
) as outcome:
    page = await crawler.arun(url)
    outcome.complete(page)
```

Capacity waits should be similarly compact:

```python
async with capacity_metrics.wait(policy=policy) as wait:
    while not acquired:
        acquired, blocked_scope = try_acquire()
        if not acquired:
            wait.blocked_by(blocked_scope)
            await listener.wait(0.25)
```

The helper records once when the operation finishes. It must use `finally` semantics for duration, must not swallow exceptions, and may accept a centralized exception classifier.

Simple lifecycle events can use typed instruments or semantic methods:

```python
task_metrics.recovered(reason="lease_expired")
cache_metrics.lookup(outcome="hit", mode=mode)
```

Avoid a free-form API that silently creates instruments from arbitrary strings. Definitions and label schemas should be declared in a catalog, and label values should use enums or `Literal` types where practical. Typos should fail type checking or raise during development rather than create a new series.

### Context

Use a context variable, analogous to `current_task_execution()`, for stable execution attributes such as primitive and worker. This avoids threading the same labels through every function.

Never automatically add high-cardinality identifiers from execution context. A run ID belongs in logs/traces even if it is convenient to attach it to every metric.

### Tests

Production and test recorders should share an interface. An in-memory recorder should support assertions without parsing Prometheus exposition text:

```python
assert recorder.counter(
    "crawl_permit_timeouts",
    blocked_scope="policy",
    policy="mercari",
) == 1

assert recorder.histogram_count(
    "crawl_permit_wait_duration",
    blocked_scope="policy",
    policy="mercari",
    outcome="acquired",
) == 1
```

Tests should verify metric semantics—especially one observation per operation, exception paths, timeout classification, and label validation—not Prometheus client internals.

## Event metrics versus authoritative state

Record events and durations where they happen in code. Derive crash-sensitive state from authoritative storage at scrape time.

Code-recorded metrics include:

- completed task and page-acquisition outcomes
- queue, capacity, page-acquisition, navigation, and execution durations
- retries, recoveries, cancellations, timeouts, and lease losses

Postgres-derived scrape metrics include:

- queue depth and oldest queued age
- running task count and active task leases
- permits in use and configured permit capacity
- live workers, worker capacity, and heartbeat age

Do not maintain these global gauges solely with paired `inc()`/`dec()` calls. A killed subprocess or worker would leave them incorrect.

### Event semantics and delivery guarantees

Code-recorded metrics are operational telemetry, not an exactly-once ledger. A child process can be killed after committing durable state but before its observation reaches the supervisor. A supervisor can also restart and reset its counters. Prometheus handles counter resets, but it cannot reconstruct a dropped observation. The scrape surface must expose `atlas_metric_observations_dropped_total{reason}` where the loss is observable, and metric transport failures must be rate-limited in logs.

Lifecycle counters have precise boundaries:

- `atlas_task_runs_total` increments once when a run reaches its final terminal state. Requeues and intermediate failed attempts do not increment it.
- `atlas_task_run_attempts_total` increments once per successful claim/attempt and records that attempt's eventual outcome.
- `atlas_task_run_recoveries_total` increments once for each recovery transition performed by the supervisor.
- `atlas_page_acquisitions_total` increments once for every completed or terminated logical page acquisition request.
- `atlas_crawls_persisted_total` increments only after the new Crawl transaction commits.

Record observations after the corresponding durable commit when one exists. These counters are sufficiently accurate for operational rates and initial SLOs, provided dropped-observation metrics remain healthy. If Atlas later requires auditable or billing-grade counts, implement a transactional outbox or calculate them from durable data rather than strengthening the in-memory metrics channel.

## Scrape ownership and topology

Atlas has two scrape surfaces with deliberately non-overlapping ownership:

1. The API exposes `/metrics` on its existing HTTP server. It owns API process/runtime metrics and cluster-wide gauges derived from Postgres plus shared deployment configuration: queue state, task leases, permit usage/capacity, and worker heartbeat/capacity state.
2. Each worker supervisor exposes `/metrics` on a dedicated HTTP port. It owns worker process/runtime metrics, supervisor counters, child event observations, duration histograms, and the worker-local portion of transient state such as capacity waiters.

Cluster-wide gauges must never also be exported by every worker. Browser capacity comes from the shared `ATLAS_BROWSER_CONCURRENCY` deployment setting, policy capacity comes from CrawlPolicy configuration, and current usage comes from active Postgres permit rows. The API and every worker must receive a consistent browser-capacity setting. Worker-local event counters, histograms, and waiter gauges are summed across worker targets. In a deployment with multiple API replicas, every replica may expose the same cluster-wide database gauges for availability; recording rules and dashboards must aggregate those gauges with `max without(instance, pod)` rather than `sum`. A future singleton exporter may take over this responsibility without changing metric names.

Browser permits measure task-scoped browser processes, not page tabs. Concurrent pages within a task share the browser and remain governed by per-run page concurrency plus per-policy permits. Mixed transport modes may require one browser permit per mode within the same task.

The API's root `/metrics` path is distinct from the existing resource-history endpoints such as `/crawls/metrics`. The initial worker defaults are `ATLAS_METRICS_ENABLED=true`, `ATLAS_METRICS_HOST=0.0.0.0`, and `ATLAS_METRICS_PORT=9090`. Container and deployment configuration must expose the worker port to Prometheus without publishing it publicly. The API uses its normal listen address and port.

Postgres-backed collection must use bounded queries and a short statement timeout. A failed collection should omit or retain no value for the affected collector, increment `atlas_metrics_collection_errors_total{collector}`, and never make the API or worker unhealthy.

The Prometheus collector is an adapter over `observability.collect_cluster_metrics(session)`, which returns a typed `ClusterMetricsSnapshot` with nested task and permit snapshots. The Atlas operations JSON endpoint calls this service directly rather than parsing Prometheus exposition or duplicating its SQL. Semantic event helpers and the central catalog remain reusable by other adapters in the same way.

### In-app operations hub

`GET /operations/metrics` and the `/scheduled-work/metrics` page provide a deliberately small in-app operational view. The response combines current cluster state from `collect_cluster_metrics`, bounded recent-window aggregates from durable task and crawl records, and optional Prometheus enrichment when `ATLAS_PROMETHEUS_URL` is configured. Supported windows are 15 minutes, 1 hour, 6 hours, and 24 hours.

The in-app page focuses on worker and browser posture, queue pressure, task outcomes and latency, crawl success and latency, failure reasons, domain health, and per-policy capacity. Resource registry and cache pages remain CRUD-oriented and link from the hub where an investigation surface exists. Atlas does not persist a second time-series dataset for this UI; Prometheus remains authoritative for complete history, alerting, waiter state, histogram-derived capacity latency, and observation-loss rates.

Per-policy capacity rows display the CrawlPolicy matcher for operator recognition. The separate immutable `metric_slug` remains the policy's Prometheus identity and stable row key; raw match expressions must not become Prometheus labels.

## Isolated subprocess transport

Each Atlas task run executes in a short-lived isolated subprocess. A normal registry in that child disappears when it exits. Do not expose an HTTP endpoint per child.

The preferred design is supervisor aggregation:

1. Application helpers create validated, compact metric observations.
2. The child sends observations over a dedicated bounded child-to-supervisor metrics queue using a non-blocking operation.
3. The worker supervisor owns the Prometheus registry and scrape endpoint.
4. The supervisor validates and applies observations to collectors.

An observation contains an instrument identifier, value, and bounded labels. It must not contain arbitrary metric definitions.

The existing result/control pipe remains separate and carries only terminal result/error messages. Metric observations must never share it, delay it, or exhaust its buffer. Each isolated slot owns a bounded `multiprocessing.Queue`; the supervisor drains it during the existing child-monitoring loop and once more after child exit. The child recorder uses `put_nowait`. If the queue is full or unavailable, it drops the observation and emits a rate-limited local log; whenever the supervisor can observe the drop, it increments `atlas_metric_observations_dropped_total{reason}`. Correctness and terminal result delivery must never depend on metric delivery.

The observation queue's initial maximum size is 1,000 per active child. Observations are intentionally compact and contain only a catalog instrument identifier, numeric value, and validated bounded labels. High-frequency polling loops must aggregate or record once per completed operation rather than enqueue on every poll.

Transient child gauges must be represented as idempotent per-child state snapshots, not blind increment/decrement events. The supervisor keeps the latest snapshot for each child, aggregates those snapshots for exposition, and removes the child's contribution on exit. A later snapshot repairs an earlier dropped snapshot; the child should also refresh non-zero transient state periodically. This prevents a killed child or lost decrement from leaving a permanent waiter count.

Prometheus client multiprocess mode is an alternative, but it introduces shared-directory lifecycle and stale-gauge cleanup concerns. Use it only if it proves materially simpler than supervisor aggregation under Atlas's short-lived process model.

Deployment, environment, service, instance, and pod labels should be attached by Prometheus target configuration rather than application call sites.

## Progress, logs, and traces

Do not derive all operational metrics from `ProgressEvent`. Progress contains user-facing URLs/resources, messages, flexible metadata, and operation IDs; mapping those mechanically to labels invites cardinality problems. A semantic operation helper may emit both progress and metrics, but they remain separate sinks with separate contracts.

- Progress: transient user experience and cancellation checkpoints.
- Metrics: bounded aggregate operations and capacity.
- Logs: detailed errors and identifiers for investigation.
- Postgres: durable run, crawl, artifact, and lease truth.
- Traces, if introduced: per-request causal timing across components.

## Initial alerts and SLO candidates

These are starting hypotheses and should be tuned from production data:

- 99% of task runs start within 30 seconds.
- 99% of capacity permits are acquired within 5 seconds.
- Page-acquisition success, excluding explicit cancellation, remains above 98%.
- `atlas_workers_stale` remains zero while at least one worker is expected, and `atlas_workers_live` remains above zero whenever queued work exists.
- Browser or policy utilization does not remain above 90% with continuous waiters.
- No queued run remains unclaimed while usable worker capacity exists.

The last condition detects queue notification, claiming, or worker-loop failures rather than ordinary saturation.

## Rollout plan

### Phase 1: execution foundations

- Add `backend/observability/`, include it in package configuration, and add the central catalog and initial histogram buckets.
- Add required immutable `metric_slug` and optional `domain_group` fields to CrawlPolicy, including migration, validation, generated defaults, and API contracts.
- Add a recorder interface, no-op recorder, in-memory test recorder, and Prometheus supervisor recorder.
- Add execution metric context and the dedicated bounded child-to-supervisor metrics queue without changing the result/control pipe.
- Expose API `/metrics` and the dedicated worker scrape port; add dependency, environment, Compose, and deployment configuration.
- Add bounded Postgres-backed cluster collectors and multiple-API aggregation recording rules.
- Add standard runtime/process collectors.

### Phase 2: critical flow metrics

- Task queue duration, execution duration, outcomes, attempts, and recovery reasons.
- Worker capacity, activity, heartbeat age, queue depth, and oldest queued age.
- Browser/policy capacity, blockers, wait duration, timeout, and lease loss.
- Page acquisition and navigation duration, source, outcome, persisted crawls, HTTP status class, and normalized failure reason.

### Phase 3: operational depth

- Cache effectiveness and artifact metrics.
- Frontier flow and useful extraction outcomes.
- Postgres, NATS, provider, and persistence dependency metrics.
- Recording rules, dashboards, and alerts based on observed distributions.

Before adding any new metric, answer:

1. What operational question will this answer?
2. Is it an event, current state, or distribution?
3. Is there already an authoritative state source?
4. Are all labels bounded?
5. What action would an operator take when it changes?
