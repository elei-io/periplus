# Worker architecture

Atlas has five independently runnable worker roles:

- acquisition stores immutable bytes, creates terminal visit evidence, and advances graph work;
- ingestion uses four serialized DuckBasin clients to commit `ingest.*` evidence;
- CDC forwards Basin DDL and per-table DML ticks into Atlas JetStream;
- materialization owns eight serialized DuckBasin clients and five fixed CDC consumers for
  `material.html_elements`, `material.jsonld_values`, `material.pages`,
  `material.page_observations`, and `material.links`;
- housekeeping removes Atlas-owned transient navigation objects and expired graph runtime state.

Ingestion never waits for materialization. Each fixed materializer acknowledges a coalesced source
tick only after its target transaction commits. Each workload resolves its exact DuckLake snapshot
window and writes a bounded delta: HTML and JSON-LD by content hash, pages and page observations by
normalized visit URL, and links by changed HTML document observation. Links retry until the
required HTML projection is committed. Existing durable consumers resume without a full backfill;
only a new non-HTML consumer backfills its target. Startup HTML reconciliation compares
content-hash identities without reparsing covered content. Horizontal replicas share the stable
durable consumer for each workload.

See [`docs_v2/LIFECYCLE.md`](../docs_v2/LIFECYCLE.md) and
[`docs_v2/CUTOFF.md`](../docs_v2/CUTOFF.md). External loading is defined in
[`docs_v2/IMPORTS.md`](../docs_v2/IMPORTS.md).
