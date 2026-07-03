# Atlas Architecture

## Current Shape

Atlas uses a simple split between API, workers, and queue:

- `atlas-api` serves the public HTTP API.
- `atlas-worker` consumes async jobs.
- `atlas-nats` provides NATS JetStream for queueing and temporary job state.

There is intentionally no separate shared browser service right now.

## Crawl Execution

Browser execution runs in-process where the crawl is executed:

- Sync requests such as `POST /index` run inside `atlas-api`.
- Async requests such as `POST /index/jobs/` are enqueued by `atlas-api` and executed inside `atlas-worker`.
- CLI commands call the same domain services in-process.

The API, worker, and CLI share domain logic written once per domain. This preserves the typed Python Crawl4AI experience across local CLI usage and server execution, while still letting production scale sync and async workloads separately.

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

For our expected shape, most production load is async. That means we can scale `atlas-worker` horizontally for scrape volume, while keeping `atlas-api` available for normal API traffic and occasional ad hoc sync crawls.

## Scaling Model

Scale these independently:

- `atlas-api`: request handling, sync/ad hoc crawls, job creation, job reads.
- `atlas-worker`: async crawl throughput and browser capacity.
- `atlas-nats`: queue durability and delivery.

The CLI is not part of the production scaling model. It is a local/operator entrypoint that imports domain services directly.

If async crawl demand grows, add more `atlas-worker` replicas. Each worker owns its own in-process browser capacity.

If sync crawl demand becomes operationally painful, revisit whether sync should become "enqueue and wait with timeout" instead of running directly inside `atlas-api`.

## API Contract

Index exposes both sync and async paths:

- `POST /index`: execute now and return results.
- `POST /index/jobs/`: enqueue async job.
- `GET /index/jobs/{job_id}`: return status and results if available.
- `GET /index/jobs/`: list jobs with high-level status info.

## Guiding Principle

Keep Atlas simple until browser execution becomes the dominant operational problem.

The queue/worker split gives us the main scaling lever we need without forcing every crawl through a separate browser service.
