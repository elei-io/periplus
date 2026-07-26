# Hazards

- Do not add compatibility aliases, dual reads or writes, legacy queue subjects, or migrations for
  disposable greenfield state.
- Do not put editable or current graph state in DuckLake.
- Do not put crawl history in Postgres.
- Do not expose a document row before its immutable object bytes are durable.
- Do not interpret documents in ingestion; interpretation belongs to fixed rebuildable
  materializations.
- Do not acknowledge Basin CDC before Atlas publication, or Atlas materialization CDC before the
  target transaction commits.
- Do not let one materialization write another materialization's target.
- Do not restore generic views, macros, user materializations, catalogue queries, agents, or the
  archived Python compiler to the runtime.
- Do not hold a Postgres advisory lock across a remote DuckLake operation.
- Do not provision streams or consumers from request paths.

The full cutoff contract is [`docs_v2/CUTOFF.md`](../docs_v2/CUTOFF.md).
