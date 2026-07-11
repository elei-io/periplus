# Hazards

These are failure patterns Atlas has encountered or is especially likely to encounter. Treat them
as design-review prompts, not as reasons to add preventive frameworks.

## Split ownership

**Putting run state or crawl history in Postgres.** It creates two authorities and couples the
control plane to high-volume execution data. Current runs belong in NATS KV; durable crawl history
belongs in DuckLake.

**Writing the same truth to multiple stores.** Mirrors eventually disagree and require repair
logic. Store one authoritative record and retain only identifiers or provenance elsewhere.

**Keeping compatibility paths after a migration.** Old models, routes, flags, and fallback reads
make the new architecture optional forever. Migrate deliberately, verify, then delete the bridge.

**Treating metrics or progress as correctness state.** Prometheus and progress events may be
missing or delayed. Correctness must rely on authoritative KV, object, Postgres, or DuckLake state.

## Accidental distributed systems

**Adding global crawl coordination.** Browser concurrency is bounded inside each worker; replicas
determine total capacity. Introduce cross-worker permits only with measured evidence that local
bounds are insufficient.

**Creating a browser service or per-action workers.** This adds protocols, deployments, and failure
modes before there is a second real need. Actions should compose the shared crawl path in-process.

**Turning index into a distributed frontier.** Index is intentionally a bounded in-memory
breadth-first walk. Web-scale crawling is a different product and should be proposed as one.

**Adding queues or abstractions for hypothetical callers.** Start with a direct implementation.
Extract a shared abstraction when a second active caller proves the common contract.

## Storage mistakes

**Letting task workers write DuckLake.** Concurrent catalogue writers complicate correctness and
operations. Workers store raw evidence and publish; the repository ingestor is the single writer.

**Creating permanent Parquet per crawl.** Small files and application-owned layout fight DuckLake
compaction. Use bounded temporary staging and let DuckLake own physical data files.

**Separating raw-object and analytical storage selection.** The stores can silently land in
different environments. One repository backend selection must configure both.

**Exposing physical paths.** Local and DuckLake paths change across deployments. Public contracts
use repository-relative object keys, document IDs, and crawl IDs.

**Mutating content-addressed objects.** A hash identity is immutable. Different bytes under the
same identity are a conflict, never an update.

## Hidden and unbounded behavior

**Creating policy state while crawling.** A missing policy should use the simple default transport.
Calibration and policy changes are explicit actions.

**Executing mutable task input.** A queued run must freeze its task revision, primitive, inputs,
policies, and schema references before execution.

**Allowing unbounded work.** HTML, elements, staging, batches, messages, task results, index breadth,
concurrency, and metric label cardinality all need explicit ceilings and clear failures.

**Hiding maintenance in request paths.** Compaction, deletion, dead-letter recovery, and large
rebuilds are explicit operator actions, not side effects of a read or crawl.

## Repository complexity

**Putting behavior in API or CLI adapters.** It produces divergent implementations. Adapters
validate and translate; actions, tasks, and repository modules own behavior.

**Preserving unused administrative surfaces.** Dashboards, metrics, models, and configuration with
no active operator or caller still impose maintenance cost. Delete them; version control is the
archive.

**Maintaining exhaustive prose inventories.** Lists of every field and environment variable drift
immediately. Document ownership and invariants; link to code and `.env.example` for exact contracts.

**Adding defensive layers without a demonstrated failure.** Factories, registries, provider
interfaces, and policy engines make the main path harder to read. Prefer visible control flow and
the smallest boundary that protects a real invariant.
