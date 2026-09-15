# Deployment

The supported development topology is Docker Compose on this MacBook. The external
homelab CDP endpoint remains the crawler acquisition boundary. Homelab is production;
this branch's cutover has not been deployed there.

Compose runs Postgres, NATS JetStream, raw S3, ClickHouse, setup, API, query,
crawler, archive ingestor, materializer, janitor, admin and public. Configure the
three API tokens and CDP endpoint in `.env` using `.env.example`, then:

```sh
make compose-up
docker compose up -d --scale periplus-materializer=2
```

Admin: localhost:8081; public: localhost:8080; API: localhost:8000;
query: localhost:8010. Infrastructure ports bind localhost. The query service has
only reader credentials; frontends hold service tokens server-side.

`periplus-setup` applies Alembic and installs the ClickHouse schema/query account.
All long-running roles reconcile their owned NATS contracts at startup. Request
handlers reuse process handles and do not provision streams or consumers.
Workers have bounded I/O and a 330-second graceful-stop allowance; uncertain
claims/draining remain protected for 610 seconds.

The Helm chart requires externally managed Postgres, ClickHouse, NATS, S3 and CDP.
It does not install databases. Its current manifests can be linted/rendered
locally, but that is not a production rollout test. The old DuckLake secrets,
experimental query deployment, parser pools and ingestion writer-lane settings
have been removed. Default autoscaling is disabled; archive/material workers
support CPU scaling or an explicitly supplied, measured demand metric.

Do not roll this branch directly over the old production deployment. Stop old
writers, preserve business and raw backups, install the new contracts, restore
from a verified archive manifest, and prove query readiness before serving it.
The destructive development migrations do not provide an old-data conversion
bridge. The local cutover's one-time conversion evidence is outside runtime code.

Ordinary parser changes need old and new materializer images concurrently until
the new candidate catches up and is published; see [REBUILDS.md](REBUILDS.md).
An image rollback cannot undo control-schema changes. Explicit maintenance
upgrades can use the chart's existing drain/setup coordination; preserve its
retry-without-automatic-rollback operational policy.

Backups and recovery: [STORAGE.md](STORAGE.md), [REBUILDS.md](REBUILDS.md).
Validation and known limits: [VALIDATION.md](VALIDATION.md).
