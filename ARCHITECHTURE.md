# Atlas Architecture

## Current Shape

Atlas currently keeps action execution direct and in-process:

- `atlas-api` serves the public HTTP API.
- `atlas` provides the local/operator CLI.
- `atlas-postgres` stores persisted task metadata and future scheduler state.

There is intentionally no queue, action-specific job API, worker, or separate browser service right now.

The proposed durable crawl, URL, artifact, and extraction-schema data model is tracked in
`CRAWL_DATA_MODEL.md`.

## Crawl Execution

Browser execution runs in-process where the action is invoked:

- API requests such as `POST /index` execute immediately inside `atlas-api`.
- CLI commands call the same action services in-process.
- Durable async orchestration will enter through `tasks` later, not through action-specific job endpoints.

The API and CLI share action logic written once and supported by shared crawler, artifact, quality, and extraction-schema utilities. This preserves the typed Python Crawl4AI experience across local CLI usage and server execution while keeping the current operational shape small.

## Why No Per-Action Queue?

The old index-specific job path made `index` special in a way that does not match the product direction. Actions are primitives: they take inputs and return outputs. Scheduling, retries, provenance, and side effects belong to tasks.

When async execution returns, it should be modeled around task creation and task runs, so every action can be orchestrated the same way.

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

User-facing actions expose synchronous execution paths only. For index:

- `POST /index`: execute now and return results.

Future queued work should be introduced through task APIs and task runs, not through `/index/jobs` or any other action-specific job route.

## Guiding Principle

Keep primitives boring. Put orchestration in tasks.
