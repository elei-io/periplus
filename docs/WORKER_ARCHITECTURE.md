# Worker architecture

Atlas has five independently runnable worker roles:

- acquisition stores immutable bytes, creates terminal visit evidence, and advances graph work;
- ingestion uses four serialized DuckBasin clients to commit `ingest.*` evidence;
- CDC forwards Basin DDL and per-table DML ticks into Atlas JetStream;
- materialization owns one shared pool of eight serialized DuckBasin clients, two source CDC
  consumers, and one durable maintenance consumer;
- housekeeping removes Atlas-owned transient navigation objects and expired graph runtime state.

Ingestion never waits for materialization. The document workload acknowledges a coalesced
`ingest.documents` tick after committing HTML elements, JSON-LD, links, and link observations in
dependency order.
The visit workload acknowledges `ingest.visits` after committing pages and page observations.
Source ownership does not reserve a client: every stage borrows from the shared pool. Pages and
page observations execute concurrently; after HTML elements commit, JSON-LD and links execute
concurrently. Maintenance borrows from the same pool only while processing a batch. Replays skip
or merge already committed stage output. Horizontal replicas share one stable durable consumer for
each source workload.

I/O-heavy fixed projections use the bounded stage contract in
`materialization.pipeline`:

```python
BoundedStagePlan(
    name="html_elements",
    target="html_elements",
    select=select_changed_documents,
    project=project_document_partition,
    write=write_element_partition,
    source_bytes=lambda document: document.content_bytes,
    item_budget=24,
    byte_budget=16 * 1024 * 1024,
    parallelism=8,
)
```

The executor partitions by items and source bytes, keeps at most one bounded parallel window in
memory, borrows shared lanes for projection, and serializes retryable writes under the exact target
lease. Stage code owns only source selection, deterministic projection, and one idempotent
partition write. HTML elements and links use this path; simpler stages may remain single-operation
stages.

The bounded links stage owns both `material.links` and `material.link_observations`: it extracts
each changed document once, streams only previously unseen pair identities, and replaces only that
document's observation slice in one remote transaction.

Backfill and rebuild requests are Postgres-owned runs delivered through Atlas JetStream. Every
maintenance delivery performs one item- and byte-bounded batch and persists its cursor after the
target commit. Backfills merge into the live target. Rebuilds write shadow tables at a pinned
snapshot, catch up later source changes in bounded batches, then take the ordinary target lease
only for a high-water recheck and atomic table swap.

See [`docs_v2/LIFECYCLE.md`](../docs_v2/LIFECYCLE.md) and
[`docs_v2/CUTOFF.md`](../docs_v2/CUTOFF.md). External loading is defined in
[`docs_v2/IMPORTS.md`](../docs_v2/IMPORTS.md).
