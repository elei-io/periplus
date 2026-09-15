# Greenfield boundary

Use one active contract. Do not add compatibility shims, dual writes, legacy
aliases or migration bridges. Prefer resetting disposable development state;
production migration requires a separately reviewed recovery plan.

Before adding a service, queue, persistence path or generic abstraction, identify
its active caller and authority. Reuse these boundaries:

- Postgres: business intent, operations and bounded recoverable execution.
- Raw archive: immutable capture facts, payloads, tombstones and recovery inputs.
- NATS: delivery and expiring coordination.
- ClickHouse: disposable material corpus and public views.

Keep one CDP acquisition primitive and one shared frontier. Do not put business
workflows in platform adapters, introduce generic resource-lock services, or
make capture wait for materialization. Public APIs are versioned by namespace
and endpoints; internal recipe/build identities remain operational details.
