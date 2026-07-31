# Atlas Helm chart

This chart deploys the Atlas API, web console, crawler, ingestors, materializers, janitor, and an
idempotent setup hook. It intentionally does not deploy PostgreSQL, NATS, S3, a CDP service, an
Ingress, or secret-management controllers; those remain platform-owned dependencies.

Every backend role uses one image tag. That image contains Atlas plus the Atlas and DuckLake CDC
extensions compiled against the exact DuckDB version in `.github/extension-sources.env`.
`atlas-setup` runs as a blocking `pre-install,pre-upgrade` hook, so catalogue and control schema
setup succeeds before Kubernetes rolls any runtime to the new image.

## Required platform contract

Provide four Kubernetes Secrets using `secrets.*` values:

| Reference | Required key | Meaning |
| --- | --- | --- |
| `controlDatabase` | `DATABASE_URL` | Standard SQLAlchemy PostgreSQL URL for editable Atlas state |
| `ducklakeMetadata` | `DATABASE_URL` | Standard PostgreSQL URL for the distinct DuckLake metadata database |
| `nats` | `NATS_URL`, `NATS_SEED` | Atlas NATS namespace credentials |
| `s3` | `ENDPOINT`, `REGION`, `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | Shared S3-compatible bucket credentials |

The control and DuckLake metadata stores must be separate PostgreSQL databases. The same S3 bucket
can hold immutable raw HTML under `config.repository.prefix` and DuckLake files under the path in
`config.ducklake.dataPath`.

The platform S3 `ENDPOINT` value is passed directly to boto3 for the raw repository and should keep
its `https://` scheme. DuckDB's S3 secret syntax instead requires `config.ducklake.s3Endpoint` as
`host:port` without a scheme; `config.ducklake.s3UseSSL` selects HTTPS.

The defaults match the secret key bundles produced by the homelab platform. Because that platform
currently creates only one `atlas` PostgreSQL database, replace it with separate `atlas_control`
and `atlas_lake` database resources before installing this chart.

## Flux example

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: atlas-chart
  namespace: atlas
spec:
  interval: 10m
  url: oci://ghcr.io/ekkuleivonen/atlas-charts/atlas
  secretRef:
    name: ghcr-pull
  ref:
    tag: 0.1.0-dev.abcdef0
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: atlas
  namespace: atlas
spec:
  interval: 10m
  chartRef:
    kind: OCIRepository
    name: atlas-chart
  values:
    imagePullSecrets:
      - name: ghcr-pull
    images:
      backend:
        tag: sha-abcdef0
      web:
        tag: sha-abcdef0
    config:
      cdpUrl: http://your-cdp-service:9222
      ducklake:
        dataPath: s3://atlas/ducklake/
        s3Endpoint: s3.elei.io:9000
```

The Atlas repository and its GHCR packages are private by default, so both Flux's OCI source and
Atlas Pods need a `kubernetes.io/dockerconfigjson` pull secret such as the homelab's `ghcr-pull`.
This can be removed if the published packages are made public.

All five metrics endpoints are exposed through annotated Services for the homelab Alloy discovery
contract. The API Service exposes `/metrics`; worker Services expose ports 9090, 9091, 9093, and
9094.
