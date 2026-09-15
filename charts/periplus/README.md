# Periplus Helm chart

Deploys application processes only. The platform supplies Postgres, ClickHouse,
NATS JetStream, raw S3 and standard CDP. See [deployment](../../docs/DEPLOYMENT.md).
This chart has been rendered/linted locally; the ClickHouse cutover has not been
deployed to homelab production.

| Secret values | Purpose |
| --- | --- |
| `controlDatabase` | Postgres business/control URL |
| `clickhouse` | ClickHouse writer username/password |
| `queryClickhouse` | Restricted reader username/password; setup provisions it |
| `nats` | NATS URL and seed |
| `s3` | Raw bucket, endpoint, region and credentials |
| `apiAccess` | Distinct admin, public and query API tokens |

Set `config.clickhouse.url` to an HTTP(S) origin and `config.cdpUrl` to the
standard CDP endpoint. Raw S3 uses the configured repository prefix. The query
process receives only reader credentials and the publication API token.

```sh
helm lint charts/periplus
helm template periplus charts/periplus --namespace periplus
```

Scaling defaults to fixed replicas. More ingestor replicas execute different
batches; each has one locally bounded lane. CPU scaling is available for imports
and materialization. Queue-derived scaling requires an explicitly measured custom
metric; removed ingestion-lane metrics must not drive production capacity.

For incompatible schema/queue changes use the explicit maintenance drain/setup
hooks and a retry-without-automatic-rollback release policy. Parser changes need
the old image's workers to remain running while the new recipe backfills. Deploy
that additional worker group deliberately; a normal single-image rolling update
does not preserve the old recipe indefinitely. See [rebuilds](../../docs/REBUILDS.md).

The shared `ingestor` replaces the former import-only and materializer deployments.
It owns one heavy execution slot per replica; imports run only when the material
queue is idle. `ingestor.image` pins the projection recipe. Optional autoscaling
has a 16-replica ceiling; size it against available cluster and ClickHouse capacity.
See `docs/DEPLOYMENT.md` before upgrading existing unqualified Postgres tables.
