# Atlas Work Plan

Atlas is greenfield. All architecture changes are hard cutovers: do not add compatibility aliases, deprecated routes, fallback execution modes, or transitional response negotiation.

## Goal: Worker-Only Action Execution

Standardize Atlas so every action execution happens in `atlas-worker`. The API and CLI must never run action, crawl, browser, or extraction work in their own processes.

The intended flow is:

1. A client submits an action to the API.
2. The API validates the request, resolves or creates the reusable task, creates a queued task run, and immediately returns its identifiers.
3. A worker claims and executes the task run.
4. The worker publishes ephemeral progress events through NATS JetStream.
5. The API relays progress to clients over SSE and exposes durable run state and results from Postgres.
6. Web and CLI clients fetch the final result after receiving a terminal event.

Postgres is the source of truth for tasks, task runs, inputs, status, final results, warnings, errors, crawls, artifacts, and timestamps. NATS is not a job database or a result store.

## Public Action API

Keep typed, action-shaped submission endpoints:

- `POST /search`
- `POST /index`
- `POST /crawl`
- `POST /extract`
- `POST /calibrate`

`POST /crawl-policies/calibrate` must be replaced by `POST /calibrate`.

Every submission endpoint returns `202 Accepted` without waiting for execution:

```json
{
  "task_id": "...",
  "run_id": "...",
  "status": "queued"
}
```

Include a `Location: /task-runs/{run_id}` response header. Although tasks are reusable, progress, results, errors, and cancellation belong to a particular task run, so clients must primarily track `run_id`.

## Task Run API

Add run-oriented endpoints shared by every action:

- `GET /task-runs/{run_id}` returns durable lifecycle metadata and status.
- `GET /task-runs/{run_id}/progress` streams ephemeral progress over SSE.
- `GET /task-runs/{run_id}/result` returns the persisted final result.
- `POST /task-runs/{run_id}/cancel` requests cooperative cancellation.

The result endpoint must clearly distinguish unfinished, successful, failed, and cancelled runs. The run metadata endpoint remains useful even after all progress messages have expired.

The progress SSE endpoint should support `Last-Event-ID` while retained messages exist, send periodic heartbeat comments, emit terminal status events, and close after a terminal event. If a client connects after progress has expired, the API should obtain current or terminal state from Postgres rather than attempting to reconstruct progress.

## Queue and Worker

Postgres remains the durable queue. API submissions create `task_runs` with `status="queued"`; only a worker may transition them to `running` and invoke the action executor.

Workers poll and claim queued rows through the lease-based task executor. Ownership lives in dedicated `task_run_leases` rows with attempt-scoped UUID fencing tokens; action transactions never mutate lease state. Transactional Postgres `LISTEN/NOTIFY` provides wake-up hints to reduce queue latency, but correctness and recovery never depend on receiving a notification.

Worker responsibilities:

- Claim queued runs safely across multiple workers.
- Maintain or renew leases during long executions.
- Recover or requeue work after expired leases according to an explicit retry policy.
- Execute all action primitives through the shared executor.
- Publish progress events to NATS JetStream.
- Persist terminal status, typed output, warnings, and errors in Postgres.
- Observe cooperative cancellation between meaningful units of work.

API and CLI code must not import or call the action executor after this cutover.

## Ephemeral Progress Through NATS

Progress is deliberately ephemeral. Use a short-retention JetStream stream with subjects shaped like:

```text
atlas.task-runs.<run_id>.progress
```

Configure its maximum message age with a duration-valued environment setting:

```env
NATS_RETENTION=5m
```

The exact default may be tuned, but it should remain low. Document that SSE reconnect/replay works only inside this retention window. There is no requirement to add a Postgres progress-event table because durable run state and results already live in Postgres.

Each progress message should use a shared envelope containing at least:

```json
{
  "event_id": "2:14",
  "run_id": "...",
  "attempt": 2,
  "sequence": 14,
  "type": "...",
  "timestamp": "...",
  "data": {}
}
```

Ordering and duplicate handling use `run_id` plus the retry-safe `attempt:sequence` event ID. Publishing progress must not determine whether execution succeeds; Postgres terminal state remains authoritative if NATS is temporarily unavailable.

Action code receives a `ProgressReporter`, never a transport callback. It emits general `ProgressEvent` records with a centrally defined `phase`, `status`, optional `resource`, `current`, `total`, `message`, `metadata`, duration, and error. `ProgressReporter.phase(...)` pairs started/succeeded/failed events and durations automatically. Cancellation checks are explicit through `reporter.check_cancelled()` rather than being a hidden side effect of event emission.

## Web Client

Replace action-specific in-request execution streams with a common asynchronous run flow:

1. Submit an action and receive `run_id`.
2. Subscribe to `/task-runs/{run_id}/progress`.
3. Render live progress while available.
4. On a terminal event, fetch `/task-runs/{run_id}/result`.
5. If the stream disconnects or its events have expired, read `/task-runs/{run_id}` and continue from durable state.

Move repeated submission, SSE, cancellation, terminal-state, and result-fetching behavior into a reusable React Query hook. Preserve action-specific types and result rendering, and keep mutation error handling through `toast.error(extractApiError(...))`.

## API-Only CLI

The CLI becomes a thin HTTP/SSE client. It must no longer open a database session or directly import and execute backend actions.

Initialize a project with:

```sh
atlas init --url https://atlas.example.com
```

This writes `atlas.json` in the current project directory:

```json
{
  "api_url": "https://atlas.example.com"
}
```

Configuration discovery starts in the current working directory and walks upward for the nearest `atlas.json`. API URL precedence is:

```text
--api-url > ATLAS_API_URL > nearest atlas.json
```

Secrets and authentication tokens must not be stored in `atlas.json`; use environment variables or an OS credential store.

Existing action commands should retain their user-facing ergonomics. Internally they submit to the API, show the run ID, consume progress SSE, fetch the result after terminal status, and return a nonzero exit code for failed or cancelled runs.

Add generic run commands as useful:

- `atlas runs get <run-id>`
- `atlas runs watch <run-id>`
- `atlas runs result <run-id>`
- `atlas runs cancel <run-id>`

## Migration Plan

- [x] Update `ARCHITECHTURE.md` to make worker-only execution the official model.
- [x] Add NATS and JetStream configuration to local Compose and application settings.
- [x] Define shared submission, progress-envelope, run-state, result, and error contracts.
- [x] Replace inline ad hoc execution with a transactionally safe enqueue operation.
- [x] Ensure all manual, scheduled, effect, retry, and backfill executions use the same queued worker path.
- [x] Publish worker progress and terminal events to the run-specific JetStream subject.
- [x] Add shared task-run cancellation.
- [x] Add shared task-run status, progress SSE, result, cancellation, and operations routes.
- [x] Change all public action endpoints to return `202` submission responses.
- [x] Move calibration from `/crawl-policies/calibrate` to `/calibrate`.
- [x] Migrate the web client to shared asynchronous task-run hooks.
- [x] Implement `atlas init`, `atlas.json` discovery, and API URL overrides.
- [x] Convert every CLI action command to API/SSE usage and remove local execution paths.
- [x] Add worker heartbeat, queue depth, oldest queued age, lease recovery, and Postgres queue wake-ups.
- [x] Add NATS health, queue latency, and execution-duration metrics.
- [ ] Remove obsolete inline execution helpers, action-specific SSE implementations, and duplicated stream parsers.
- [ ] Update Compose, documentation, examples, and tests for the new required API/worker/NATS topology.

## Cutover Rules

Prefer a deliberate hard cut rather than maintaining permanent synchronous and queued modes. During migration, do not create a second action implementation: both old and new callers must use the existing task/action executor until the old callers are removed.

Completion means:

- No user-facing action executes in the API process.
- No action executes locally in the CLI process.
- Every execution has a persisted task run claimed by a worker.
- Progress is available live through the API while JetStream retains it.
- Final status and results remain available from Postgres after progress expires.

## Phase 2: Reliable Task Lifecycle

- [x] Persist cooperative cancellation and expose `POST /task-runs/{run_id}/cancel`.
- [x] Stop queued runs immediately and running runs at progress or heartbeat boundaries.
- [x] Renew active leases and fence completion by worker ownership.
- [x] Recover expired leases with a bounded attempt policy.
- [x] Persist worker heartbeats and graceful stopping state.
- [x] Publish transactional Postgres trigger wake-ups while retaining Postgres polling.
- [x] Expose queue depth, oldest queue age, worker state, latency, duration, and NATS health.
- [x] Add generic `atlas runs get|watch|result|cancel` commands.
- [x] Make web and CLI clients fall back to durable task state when progress disconnects.
- [x] Add lifecycle tests and an opt-in database-backed enqueue/cancel/operations and lease-fencing smoke test.

## Phase 3: Concurrent Workers and Global Crawl Capacity

Support both bounded concurrency inside each worker process and horizontal scaling through multiple worker processes. These solve different problems and must compose safely:

```text
total task-run capacity = worker replicas × concurrency per worker
```

Configure per-process task capacity with:

```env
ATLAS_WORKER_CONCURRENCY=4
```

The worker acts as a supervisor with a bounded set of execution slots. It claims runs with transactional `FOR UPDATE SKIP LOCKED` until its slots are full, then executes each claimed run in an independent, killable subprocess with its own async event loop and database sessions. A blocked browser, provider, or synchronous database call cannot freeze the supervisor or sibling runs. Idle workers do not spawn execution subprocesses.

On shutdown, a worker stops claiming, waits for the configurable grace period, kills remaining execution subprocesses, and atomically relinquishes their exact fenced leases. Planned shutdown requeues unfinished work without consuming failure retry budget. Cancellation uses the supervisor's run/token mapping to kill only the matching subprocess and become terminal without waiting for lease expiry.

Worker heartbeat state supports multiple active runs without embedding run IDs. Store worker presence and capacity information, and derive active ownership by joining running task rows to `task_run_leases` for the worker. Useful heartbeat fields include `capacity` and `active_run_count`.

### Global Crawl-Policy Concurrency

Worker concurrency limits task runs per process. Crawl-policy concurrency limits real page acquisition across the entire Atlas deployment. A policy limit must not be multiplied by either `ATLAS_WORKER_CONCURRENCY` or the number of worker replicas.

For example, a policy with `max_concurrency = 2` allows only two matching page loads globally, even when several workers and task runs are active.

Enforce global limits at the shared `actions.crawl` page-acquisition chokepoint so they cover search, index, crawl, extract, calibration, scheduled runs, and effect-triggered runs. Acquire a permit only after cache lookup and in-flight deduplication; cache hits must not consume browser capacity.

The effective acquisition rule is:

```text
worker task slot available
AND deployment-wide browser permit available
AND matching crawl-policy permit available
```

Use Postgres-backed renewable permits rather than process-local semaphores or connection-bound advisory locks. A permit record should contain enough state to recover it after worker failure:

```text
crawl_permits
- id
- policy_id
- holder_worker_id
- task_run_id
- url_id
- acquired_at
- leased_until
```

Permit acquisition must transactionally remove or ignore expired permits, enforce the configured capacity for the matched policy, and insert a leased permit only when capacity is available. Permit release should wake waiters through a best-effort notification, while polling remains the correctness fallback. Permit leases must be renewed during long page loads and released in `finally` blocks.

The matched crawl policy is the concurrency scope. All URLs resolved to one policy share its `max_concurrency` bucket; narrower or separate URL matches use separate policies and therefore separate buckets. Keep deployment-wide browser capacity separate:

```env
ATLAS_BROWSER_CONCURRENCY=12
```

Task runs waiting for permits must wait asynchronously, remain cancellation- and lease-aware, use an explicit timeout, and emit a `waiting_for_crawl_capacity` progress state. It is acceptable initially for a waiting run to occupy a worker task slot; introduce separate runnable/browser slot accounting only if observed contention justifies it.

### Phase 3 Delivery Checklist

- [x] Add `ATLAS_WORKER_CONCURRENCY` with a bounded, nonzero default.
- [x] Refactor the worker into a supervisor that fills and replenishes execution slots.
- [x] Give each concurrent run an isolated subprocess, fenced lease, progress publisher, and killable failure boundary.
- [x] Update worker heartbeats and operations reporting for capacity and multiple active runs.
- [x] Implement graceful concurrent-worker shutdown and recovery.
- [x] Verify multiple worker processes safely share the same Postgres claim queue.
- [x] Define crawl-policy `max_concurrency`; the URL match is its scope.
- [x] Add renewable Postgres-backed crawl permit records and acquisition service.
- [x] Enforce permits only around actual page acquisition in `actions.crawl`.
- [x] Add deployment-wide `ATLAS_BROWSER_CONCURRENCY` permits.
- [x] Make permit waiting asynchronous, cancellable, lease-aware, observable, and timeout-bounded.
- [x] Add wake-ups on permit release with polling fallback.
- [ ] Test concurrent task progress and results through API, web, and CLI.
- [x] Test that policy limits remain global across both worker slots and worker replicas.
- [x] Test permit recovery after worker death and guarantee permits are released after failures and cancellation.
- [x] Measure database pool requirements and document sizing relative to worker concurrency.

The supervisor uses its own short-lived control-plane connections. Each active execution subprocess owns a separate pool and normally needs at most two simultaneous connections: one action/checkpoint session and one lease or permit renewal. Subprocess exit closes its pool. Revisit pool sizing before raising concurrency or worker replicas; budget roughly two live connections per active run plus supervisor/API overhead rather than multiplying the configured pool maximum blindly.
