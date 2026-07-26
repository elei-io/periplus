# Architecture

The current Atlas architecture is defined by:

- [`docs_v2/VISION.md`](../docs_v2/VISION.md)
- [`docs_v2/SCHEMA.md`](../docs_v2/SCHEMA.md)
- [`docs_v2/LIFECYCLE.md`](../docs_v2/LIFECYCLE.md)
- [`docs_v2/CUTOFF.md`](../docs_v2/CUTOFF.md)

The active delivered path is:

```text
crawl graph -> immutable bytes -> ingest.* -> CDC -> material.*
```

Postgres owns editable control state and current graph execution. NATS owns work delivery,
ephemeral presence, scoped leases, and CDC events. DuckLake owns immutable observed evidence
and rebuildable Atlas materializations. The object repository owns content-addressed source bytes.

The Python compiler is archived outside the runtime and the public `web.*` semantic interface is not
currently delivered.
