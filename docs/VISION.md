# Vision

Atlas turns changing web pages into durable, queryable evidence.

A crawl should be useful beyond the request that caused it. Atlas retains immutable raw HTML and a
versioned structural projection so later searches, extractions, and analyses can reuse the same
evidence. Crawl policies and extraction schemas are explicit, editable knowledge rather than
hidden behavior inside a browser call.

The system is designed for a small team. It should be understandable end to end, operate with
bounded resources, and make state ownership obvious. New machinery must solve a demonstrated
problem, not a hypothetical future scale problem.

## What Atlas does

- Acquires pages through one crawl path.
- Searches and walks sites within explicit bounds.
- Produces structured data from retained pages.
- Reuses explicit crawl policies and data/query schemas.
- Preserves raw evidence and a queryable DOM representation.
- Runs scheduled or ad hoc work with inspectable current state and recovery paths.

## What Atlas is not

- A web-scale distributed crawler.
- A general workflow orchestration platform.
- A browser farm or standalone browser-service framework.
- A general-purpose data warehouse.
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
