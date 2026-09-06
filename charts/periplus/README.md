# Periplus Helm chart

This chart deploys the Periplus API, admin application, public application, crawler, ingestors, materializers, janitor, and an
idempotent setup hook. It intentionally does not deploy PostgreSQL, NATS, S3, a CDP service, an
Ingress, or secret-management controllers; those remain platform-owned dependencies.

Every backend role uses one image tag. That image contains Periplus plus the Periplus and DuckLake CDC
extensions compiled against the exact DuckDB version in `.github/extension-sources.env`.
`periplus-setup` runs as a blocking `pre-install,pre-upgrade` hook, so catalogue and control schema
setup succeeds before Kubernetes rolls any runtime to the new image.

## Required platform contract

Provide four Kubernetes Secrets using `secrets.*` values:

| Reference | Required key | Meaning |
| --- | --- | --- |
| `controlDatabase` | `DATABASE_URL` | Standard SQLAlchemy PostgreSQL URL for editable Periplus state |
| `ducklakeMetadata` | `DATABASE_URL` | Standard PostgreSQL URL for the distinct DuckLake metadata database |
| `nats` | `NATS_URL`, `NATS_SEED` | Periplus NATS namespace credentials |
| `s3` | `ENDPOINT`, `REGION`, `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | Shared S3-compatible bucket credentials |

The control and DuckLake metadata stores must be separate PostgreSQL databases. The same S3 bucket
can hold immutable raw HTML under `config.repository.prefix` and DuckLake files under the path in
`config.ducklake.dataPath`.

The platform S3 `ENDPOINT` value is passed directly to boto3 for the raw repository and should keep
its `https://` scheme. DuckDB's S3 secret syntax instead requires `config.ducklake.s3Endpoint` as
`host:port` without a scheme; `config.ducklake.s3UseSSL` selects HTTPS.

The defaults match the secret key bundles produced by the homelab platform. Because that platform
currently creates only one `periplus` PostgreSQL database, replace it with separate `periplus_control`
and `periplus_lake` database resources before installing this chart.

## Flux example

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: periplus-chart
  namespace: periplus
spec:
  interval: 10m
  url: oci://ghcr.io/ekkuleivonen/periplus-charts/periplus
  secretRef:
    name: ghcr-pull
  ref:
    tag: 0.1.0-dev.abcdef0
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: periplus
  namespace: periplus
spec:
  interval: 10m
  chartRef:
    kind: OCIRepository
    name: periplus-chart
  values:
    imagePullSecrets:
      - name: ghcr-pull
    images:
      core:
        tag: sha-abcdef0
      admin:
        tag: sha-abcdef0
      public:
        tag: sha-abcdef0
    config:
      cdpUrl: http://your-cdp-service:9222
      ducklake:
        dataPath: s3://periplus/ducklake/
        s3Endpoint: s3.elei.io:9000
```

The Periplus repository and its GHCR packages are private by default, so both Flux's OCI source and
Periplus Pods need a `kubernetes.io/dockerconfigjson` pull secret such as the homelab's `ghcr-pull`.
This can be removed if the published packages are made public.

All five metrics endpoints are exposed through annotated Services for the homelab Alloy discovery
contract. The API Service exposes `/metrics`; worker Services expose ports 9090, 9091, 9093, and
9094.

The three image keys are `images.core`, `images.admin`, and `images.public`; pin all three.
Create the externally managed `periplus-api-access` secret with distinct `ADMIN_API_TOKEN`,
`PUBLIC_API_TOKEN`, and `PUBLIC_RECEIPT_SECRET` keys (the receipt secret needs at least 32
characters). Configure alternative names through `secrets.apiAccess`.
Route public and admin through separate TLS ingresses. Admin login is `admin` with the
administrative token as password. Keep the core API private and enforce aggregate public
rate limits at ingress. No database or service token is supplied to the public browser.
