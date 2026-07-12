# Vision

Atlas turns changing web pages into durable, queryable evidence.

A crawl should be useful beyond the request that caused it. Atlas retains immutable raw HTML and a
versioned structural projection so later searches, extractions, and analyses can reuse the same
evidence. Crawl policies, graph topology, edge SQL, and analytical schemas are explicit, editable
knowledge rather than hidden behavior inside a browser call or action-specific traversal loop.

The system is designed for a small team. It should be understandable end to end, operate with
bounded resources, and make state ownership obvious. New machinery must solve a demonstrated
problem, not a hypothetical future scale problem.

## What Atlas does

- Acquires pages through one crawl path.
- Composes bounded searches, pagination, and site walks as crawl graphs over retained evidence.
- Produces structured data from retained pages.
- Reuses explicit crawl policies, catalogue SQL, and analytical schemas.
- Preserves raw evidence and a queryable DOM representation.
- Publishes derived web evidence as stable, typed tables that downstream data tools can consume
  through snapshots or incremental changes.
- Runs scheduled or ad hoc crawl graphs with inspectable current state and recovery paths.

## What Atlas is not

- A web-scale distributed crawler.
- A general workflow orchestration platform.
- A browser farm or standalone browser-service framework.
- A general-purpose data warehouse.
- A destination connector platform or general data-pipeline orchestrator.
- A collection of per-action workers, queues, and storage systems.
- A compatibility museum for storage models the project has left behind.
- A reason to retain every possible media format or analytical projection.

## Product principles

1. Preserve evidence; derive views from it.
2. Give each kind of state one authoritative owner.
3. Prefer a direct, bounded implementation over a framework.
4. Make expensive or stateful behavior explicit.
5. Scale worker replicas before inventing distributed coordination.
6. Delete completed migration scaffolding and unused surfaces.

Crawl graphs do not weaken the workflow-orchestration boundary. Their nodes only map admitted URL
inputs to crawl work, and their edges only derive further URL inputs from retained crawl evidence.
They do not run arbitrary compute, destination connectors, or external side effects.
