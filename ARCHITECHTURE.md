# Atlas Architecture

## Current Shape

Atlas currently keeps action execution in-process and task-backed:

- `atlas-api` serves the public HTTP API.
- `atlas` provides the local/operator CLI.
- `atlas-postgres` stores persisted task metadata and future scheduler state.

There is intentionally no action-specific job API, per-action worker, or separate browser service
right now.

The proposed durable crawl, URL, artifact, and extraction-schema data model is tracked in
`CRAWL_DATA_MODEL.md`.

## Crawl Execution

Browser execution runs in-process where the action is invoked:

- API requests such as `POST /index` resolve or create a task, create a task run, and execute
  that run inline inside `atlas-api`.
- CLI commands follow the same path: resolve or create a task, create a task run, and execute the
  run inline in the CLI process.
- Scheduled or queued execution uses the same task-run executor path instead of a separate action
  implementation.

Ad hoc API and CLI runs are not enqueued for the worker. They create manual task runs that start
immediately in the current process. Their tasks are created without schedules, so the scheduler
does not pick them up unless a user later adds a schedule.

The task executor is responsible for marking runs finished, recording warnings, creating
artifacts, and writing durable crawl, URL, artifact, and extraction-schema metadata. API and CLI
layers should stay thin and should not bypass task-run persistence for user-facing actions.

This keeps playground and ad hoc usage production-shaped: a user can try an action from the UI,
API, or CLI, then later enable a schedule on the created task if the result is useful.

Execution policy also belongs on this shared path. Concurrency, per-domain limits, in-flight
dedupe, cache reuse, and warning tolerance should be enforced by task/crawl services used by API,
CLI, scheduler, and worker execution alike.

## Why No Per-Action Queue?

The old index-specific job path made `index` special in a way that does not match the product
direction. Actions are primitives: they take inputs and return outputs. Scheduling, retries,
provenance, and side effects belong to tasks.

Ad hoc execution should still use task creation and task runs; it just executes the run inline
instead of waiting for an external worker.

## Why Not A Separate Browser Service?

We considered a separate `atlas-browser` Crawl4AI service, but it added complexity without enough benefit for the current workload shape.

The separate service gave us:

- Isolated browser crashes and memory pressure.
- A single browser runtime surface to tune.
- Independent browser capacity scaling.

But it cost us:

- Another network hop and failure mode.
- Loss of some typed Crawl4AI Python ergonomics.
- Less feature parity with in-process Crawl4AI configuration.
- More Compose and production wiring.
- Harder local debugging.

For now, action execution stays close to the caller. If browser execution becomes the dominant operational problem, we can revisit the service boundary with evidence.

## API Contract

User-facing action endpoints may remain synchronous, but they should execute through the task-run
path. For index:

- `POST /index`: resolve or create task, create run, execute run inline, return run output.

Queued work should continue to enter through task APIs and task runs, not through `/index/jobs` or
any other action-specific job route.

## Guiding Principle

Keep primitives boring. Put orchestration in tasks.
