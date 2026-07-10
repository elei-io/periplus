# Atlas Architecture

## Execution Boundary

Atlas has exactly one action-execution path. API and CLI processes never execute actions, browser work, crawls, schema generation, or extraction locally.

1. A client submits a typed action request to `atlas-api`.
2. The API resolves or creates a reusable task, inserts a queued task run in Postgres, and returns `202 Accepted` with `task_id` and `run_id`.
3. `atlas-worker` claims the queued run using a database lease and executes it through the shared task executor.
4. The worker persists status, warnings, errors, results, crawls, URLs, schemas, and artifacts in Postgres.
5. During execution, the worker publishes ephemeral progress to NATS JetStream.
6. The API relays retained/live progress over SSE. Clients fetch the durable result after a terminal event.

Postgres is the durable queue and source of truth. NATS is only a short-lived progress channel; failure or expiry of progress messages does not erase task state or results.

## Public API

The typed submission endpoints are:

- `POST /search`
- `POST /index`
- `POST /crawl`
- `POST /schema`
- `POST /extract`
- `POST /calibrate`

They return a common queued-run contract and a `Location: /task-runs/{run_id}` header. There are no synchronous response modes and no action-specific SSE streams.

Run state is exposed through:

- `GET /task-runs/{run_id}`
- `GET /task-runs/{run_id}/progress`
- `GET /task-runs/{run_id}/result`
- `POST /task-runs/{run_id}/cancel`
- `GET /task-runs/operations/summary`

Tasks are reusable definitions; task runs are individual executions. Progress, results, and errors are therefore addressed by run ID.

## Progress

Workers publish envelopes to `atlas.task-runs.<run_id>.progress`. JetStream retains them for the duration configured by `NATS_RETENTION`, which defaults to `5m`. Event IDs use `attempt:sequence`, so retries of one run cannot collide with retained messages from an earlier attempt. SSE supports replay within that window using `Last-Event-ID`; after expiry, clients use durable task-run status and results from Postgres.

Actions receive a transport-independent `ProgressReporter`. General `ProgressEvent` records use a defined phase taxonomy and optional resource, counts, message, metadata, duration, and error fields. Reporter phase contexts pair lifecycle events automatically, while cancellation checks remain explicit.

Progress delivery is best effort. It must not decide execution success. Terminal task state in Postgres is authoritative.

Postgres emits transactional `NOTIFY atlas_task_runs_queued` wake-ups through triggers whenever a task run enters the queued state. They never grant ownership: workers still claim rows transactionally and poll as a fallback. NATS is not involved in queueing or worker wake-ups.

## Execution Lifecycle

Running work uses renewable records in the dedicated `task_run_leases` table. Each claim creates a random lease token and attempt number. Heartbeats touch only the lease record; action transactions never mutate orchestration ownership. Completion and durable checkpoints are fenced by the token, so a worker that lost its lease cannot overwrite a later attempt. Expired work is requeued until its configured maximum attempt count, then failed.

Each action run executes in an isolated, killable subprocess with its own async event loop. Synchronous database, browser, or provider stalls therefore cannot block the worker supervisor, other action slots, recovery, process heartbeats, or graceful shutdown. Control-plane database work runs outside the supervisor event loop and uses bounded lock waits.

Action persistence must use short transactions. No database transaction may remain open across browser work, LLM calls, capacity waits, or other unbounded awaits. `task_runs` changes only at lifecycle transitions; intermediate crawl, artifact, and schema provenance belongs to the corresponding domain tables.

Cancellation is cooperative and durable. Queued runs cancel immediately; running runs stop at explicit reporter checks, capacity waits, or the next worker heartbeat. Disconnecting an SSE client does not cancel execution.

## Concurrency and Crawl Capacity

Each worker supervises a bounded number of task runs configured by `ATLAS_WORKER_CONCURRENCY` (default `4`). Multiple worker processes share the same Postgres queue; transactional `FOR UPDATE SKIP LOCKED` claims remain the ownership boundary. Heartbeats report process capacity and active-run count, while active ownership is derived from task-run leases.

Task concurrency is separate from page-acquisition concurrency. Every real browser load acquires a deployment-wide permit configured by `ATLAS_BROWSER_CONCURRENCY` (default `12`) and, when a matching crawl policy defines `max_concurrency`, a global permit keyed by that policy. The policy's URL match is the concurrency scope: all URLs resolved to the same policy share its absolute limit across every run and worker.

Permits are renewable Postgres leases acquired only after cache lookup. Cache hits do not consume capacity. Waiting is asynchronous and cancellation-aware; expired permits are reclaimed, release emits a best-effort Postgres notification, and polling remains authoritative.

## CLI

The `atlas` CLI is an HTTP/SSE client. `atlas init --url <api-url>` writes `atlas.json` in the current project directory. Configuration is resolved in this order:

```text
--api-url > ATLAS_API_URL > nearest atlas.json
```

The CLI does not connect to Postgres or import the action executor.

## Code Ownership

- `backend/actions/` owns primitive behavior.
- `backend/tasks/` owns durable orchestration, queueing, leases, and execution.
- `backend/worker/` owns scheduler polling and worker execution.
- `backend/api/` validates HTTP input and exposes task-run state; it does not execute actions.
- `backend/cli/` talks only to the HTTP API.
- `backend/artifacts/` and the durable model packages own crawl and result metadata.

Crawl remains the page-acquisition chokepoint. Shared page-loading configuration stays in `actions.shared.crawl`; no action may create a parallel browser execution path.

## Operational Requirements

The API can remain healthy while workers are unavailable, so operations must eventually expose worker heartbeat, queue depth, oldest queued age, lease expiry/recovery, NATS health, queue latency, and execution duration. Correctness must not depend on Postgres notification delivery; workers poll for queued runs as a fallback.
