# Worker architecture

Atlas has five independently runnable worker roles:

- acquisition stores immutable bytes, creates terminal visit evidence, and advances graph work;
- ingestion uses four serialized DuckBasin clients to commit `ingest.*` evidence;
- CDC forwards Basin DDL and per-table DML ticks into Atlas JetStream;
- materialization owns eight serialized DuckBasin clients and four fixed CDC consumers for
  `material.html_elements`, `material.jsonld_values`, `material.pages`, and `material.links`;
- housekeeping removes Atlas-owned transient navigation objects and expired graph runtime state.

Ingestion never waits for materialization. Each fixed materializer acknowledges a coalesced source
tick only after its whole-table target refresh commits. Horizontal replicas share the stable
durable consumer for each workload.

See [`docs_v2/LIFECYCLE.md`](../docs_v2/LIFECYCLE.md) and
[`docs_v2/CUTOFF.md`](../docs_v2/CUTOFF.md).
