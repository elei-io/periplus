# Architecture

Periplus has three kinds of persistence and one delivery system:

| Layer | Owns | Needed to restore the corpus? |
| --- | --- | --- |
| Raw object archive | Immutable capture facts, payloads, tombstones, recovery manifests and software | Yes |
| Postgres | Customer requests, policies, current execution, collection results, import/rebuild controls and publication | Fresh empty Postgres is sufficient for corpus recovery; restore its backup to recover the business |
| NATS JetStream/KV | Work delivery, operation leases, crawler presence and domain permits | No; reconcile delivery from Postgres and the raw journal |
| ClickHouse | Derived captures, documents and public SQL views | No; rebuild from the archive |

```text
Collection / schedule -> Postgres frontier -> crawler -> CDP
                                              |
Common Crawl import -> ingestor ---------------+-> immutable raw archive
                                                        |
                                             optional NATS notification
                                                        |
                         archive journal -> bounded materializer batches
                                                        |
                                                ClickHouse material
                                                        |
                                                public_v1 query API
```

The raw capture says what was observed: URL, observation time and precision,
HTTP status, completeness, content identity and source provenance. It does not
say who requested it, what they paid, which retry ran, or which collection it
fulfilled. Those associations belong in Postgres `collection_results`.
A WARC source record carries archival provenance, not customer intent.

The crawler writes payload bytes before freezing its capture in the transactional
frontier outbox. Its relay commits the capture into the raw journal and announces
it through NATS. Collection traversal does not wait for ClickHouse. Ingestors
process recoverable archive-import jobs; they use the same archive publisher.
There is no separate ClickHouse ingestion/evidence database.

Materializers reconcile raw journal positions even when every notification is
missing. One planner per software recipe schedules bounded historical and live
ranges. Every matching worker can execute batches. Postgres checkpoints advance
only after verified ClickHouse writes. Completed batch records are removed;
there is no permanent per-capture rebuild ledger in Postgres.

A hidden rebuild receives new captures while historical work runs. Publication
changes one Postgres pointer atomically. The query service pins that pointer once
per request and reads only the selected public views. Workers never rewrite a
serving table into a new parser interpretation. See [REBUILDS.md](REBUILDS.md).

## Code ownership

- `crawl/control/`: collection intent, policies and schedules.
- `crawl/runtime/`: frontier, budgets, shared acquisitions, outbox and politeness.
- `crawl/acquisition/`: one standard-CDP acquisition and its operational record.
- `ingestion/`: capture/archive contracts, immutable objects and Common Crawl imports.
- `materialization/`: HTML projections, ClickHouse storage and rebuild workers.
- `query/`: bounded ClickHouse SQL validation, execution and publication binding.
- `retention/`: exact write claims and capture retirement requests.
- `operations/`: janitor, admin APIs, access controls and private query history.
- `platform/`: process lifecycle and Postgres, ClickHouse and NATS adapters.
- `entrypoints/`: thin process composition.

DuckDB remains only for bounded page-local navigation/follow SQL. Corpus queries
run in ClickHouse. There is no DuckLake, CDC extension, lake compactor, Parquet
publication stage or second ingestion receipt service.

Frontends and SDKs use HTTP only. Query processes receive read-only ClickHouse
credentials and an API token for publication lookup; no raw-store, Postgres or
NATS credentials. The standard CDP service remains externally owned.
