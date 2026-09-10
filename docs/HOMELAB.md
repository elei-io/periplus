# Homelab onboarding

This is the application-side contract for a first private deployment, followed by
public launch. Flux, external grants, SOPS credentials, Cloudflare, backups and
LakeDucktor deployment belong to the homelab repository. Do not recreate existing
control or lake databases. Homelab runbooks remain authoritative for live endpoints
and capacity; this example must be reviewed when those change.

## Install order

1. Pin a published chart and matching core/admin/public images. Set the repository
   variable `PERIPLUS_PUBLIC_ORIGIN` before building the public image; see
   [deployment](DEPLOYMENT.md#public-search-metadata). An unset origin emits noindex.
2. In `applications`, provision the seven Secret bundles in the
   [chart contract](../charts/periplus/README.md#required-platform-contract), plus
   `ghcr-pull` if packages are private. SOPS is the source of credentials. Writer
   and reader identities must be different. NATS requires a user NKey public key
   registered in a dedicated account, with its corresponding private seed in
   `NATS_SEED`; `NATS_URL` is `nats://nats.state.svc:4222`. Do not put the private
   seed in server configuration. The raw S3 Secret's `ENDPOINT` is
   `http://192.168.40.101:7070`, `REGION` is `us-east-1`, and `BUCKET` is `periplus`.
3. Apply [homelab values](../charts/periplus/examples/homelab-values.yaml) to a
   HelmRelease named `periplus` in `applications`. Use `spec.timeout: 20m` so Helm
   can wait through the setup Job's 900-second deadline. Flux defaults to five
   minutes ([reference](https://fluxcd.io/flux/components/helm/api/v2/)). Arrange
   platform reconciliation dependencies so database owners, NATS, S3 and Secrets
   exist before Helm starts; a Helm hook does not provision them.
4. Setup installs the catalogue. Once its `ducklake` schema exists, apply the reader
   grants below. Query may remain unready until then; grant provisioning must not
   wait on HelmRelease readiness, which would create a dependency cycle. The hook
   is deleted after success; retain its logs through normal logging.
5. Keep ingress private. With crawler replicas still zero, use the admin UI's
   frontier controls to set `dispatch_limit=2`, a suitable `capture_timeout_ms`, and
   `paused=true`. The underlying API is `GET /frontier/controls`, followed by
   `PUT /frontier/controls` with `{expected_version: policy_version, settings: ...}`.
   Preserve the other returned settings. Version conflicts require a fresh GET.
6. Change `crawler.replicas` to 1 in Git. Resume only for the bounded acceptance
   crawl below. Runtime controls persist in Postgres; setup preserves them. Repeat
   the control step after a fresh-database recovery.

## Capacity

The initial values disable autoscaling and use one replica per role, with one
writer lane per ingestor/materializer and a two-connection control pool per Python
process (`max_overflow=0`). This reduces pool demand; it is not a complete
PostgreSQL connection ceiling. DuckDB metadata connections are additional and
materializer coordination can open connections alongside its batch lane. Measure
`pg_stat_activity` by database/user during setup, a crawl and a rebuild, allowing
room for Stolosio, administration and rolling replacements before increasing load.

Crawler executor lanes are code-owned at 12; there is no crawler concurrency Helm
value. The Postgres frontier `dispatch_limit` bounds shared dispatched acquisitions
across replicas, including rollout overlap. Start at two. Dispatch also probes CDP,
and uncertain remote attempts may outlive a client, so this is not a physical
browser-session cap. Stolosio must enforce its own six-session fleet ceiling and
leave room for probes, other clients and recovery. Do not infer a six-session
budget from one crawler replica. Pause stops new authorization and lets started
captures finish; confirm remote sessions have drained before disruptive recovery.

## Query reader privileges

Provision a fresh `periplus_lake_reader` LOGIN role through CNPG, without owner-role
membership or other grants. After setup, run
[grants.sql](../charts/periplus/examples/query-reader/grants.sql) in `periplus_lake`
as an administrator. The grant uses `FOR ROLE periplus_lake`, because default
privileges follow the role creating each table, not the administrator applying
permissions ([PostgreSQL reference](https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html)).
Repeat that default grant for any additional metadata DDL creator, including a
separate maintenance identity if it creates tables. Reapply existing-table grants
after restoring a catalogue. Do not grant the reader membership in a writer role.
`default_transaction_read_only` is defense in depth; actual ACLs enforce isolation.
Audit inherited/PUBLIC grants and callable privileged functions when using an
existing database; this script does not remove unrelated privileges globally.

Provision an ordinary Versity user, not an admin or bucket owner. Merge the
[reader statement](../charts/periplus/examples/query-reader/s3-policy.json) into
the bucket's existing policy after replacing its principal with the reader access
key ID. Preserve other statements. The reader gets GetObject on `ducklake/*` only,
with no ListBucket, raw repository access, PutObject or DeleteObject. Do not run
`docker/query/bootstrap.py` against homelab: it provisions Compose's `lake` bucket
and replaces its bucket policy. Query pods must have no ambient writer credentials.

Validate on disposable data with actual reader credentials:

- Read an existing metadata table; create a probe table as `periplus_lake` after
  applying grants and read it as the reader, proving future-table access.
- Turn transaction read-only off in the test session, then attempt INSERT, UPDATE,
  DELETE, TRUNCATE and CREATE in `ducklake`; all must fail by permission. Attempt
  SET ROLE to the writer; it must fail. Remove the probe as the owner.
- Upload a small lake-prefix probe as writer. Reader GET must succeed; reader PUT
  to a new key and DELETE of the probe must fail. Reader GET of a writer-created
  repository-prefix probe must fail. Remove both probes with writer credentials.
- Execute a bounded query through the query service over an actual registered
  Parquet file. This verifies DuckLake access beyond SQL grants or policy syntax.

## NATS storage and failure contract

Processes reconcile their own topology at startup. Account limits must admit all
three streams and five KV streams below; do not copy Stolosio's two-stream limit.
With homelab values, all eight streams (including KV) use three replicas.

| Stream / KV bucket | Logical limit | Replicas | Retention |
| --- | --- | --- | --- |
| `PERIPLUS_CATALOGUE_WORK` | 512 MiB | 3 | Work queue; no age expiry; reject new on full |
| `PERIPLUS_DEAD_LETTER` | 256 MiB | 3 | 30 days |
| `PERIPLUS_CRAWL_WORK` | 32 MiB | 3 | Work queue; reject new on full |
| `periplus_ingestion_results` | 256 MiB | 3 | 7 days |
| `periplus_domain_pacing` | 256 MiB | 3 | No TTL |
| `periplus_catalogue_workers` | 64 MiB | 3 | 30 seconds |
| `periplus_crawler_workers` | 1 MiB | 3 | 30 seconds |
| `periplus_catalog_operations` | 64 MiB | 3 | 30 seconds |

KV creates `KV_`-prefixed streams. Budget replicated bytes, not just logical
payload: these limits total about 4.22 GiB with replica copies. Start with at
least 6 GiB account file budget and eight streams, and check actual reservation
and server capacity alongside other accounts before installation. Keep memory
storage disabled. Check the rendered account's limits and NATS stream reports;
consumer/payload overhead and other account usage need headroom.

`config.nats.operationalReplicas` sets `PERIPLUS_NATS_OPERATIONAL_REPLICAS` for
capture delivery, both presence stores, domain pacing and operation leases.
Helm defaults to three; local development defaults to one. All processes sharing
an account must agree. Startup rejects existing replica-count mismatches and does
not delete or automatically reconfigure stores. For an existing account, quiesce
clients, back up the account, explicitly update each affected stream's replicas
with the supported NATS tooling, wait for replica synchronization, then deploy the
matching configuration. Preserve names, messages and KV values. A new homelab
account is created at three replicas from the start.

Postgres retains frontier intent/outbox and materialization receipts. Expired
capture dispatches are recovered; retained ingestion jobs are republished when
receipts are missing. DuckLake owns the CDC cursor. These mechanisms support
worker restart, but are not proof of lossless reconstruction after total account
loss: dead-letter history and pacing state can be lost, and published materializer
work needs recovery validation. Do not advertise an empty-account restart as a
supported lossless restore. Include the Periplus account in backup and perform the
coordinated restore below. The platform-account backup does not include it.

## Lake maintenance and coordinated recovery

LakeDucktor must attach the same `periplus_lake` metadata database/schema and
`s3://periplus/ducklake/` with writer-capable credentials, the same HTTP endpoint,
path style and compatible DuckDB/DuckLake versions. Periplus does not own its
chart or configuration schema; use the pinned LakeDucktor release's documented
settings. Never give maintenance credentials to query. Keep snapshot expiry and
orphan deletion disabled until recovery horizons and a restore drill are established.
For the current LakeDucktor source, the non-secret connection settings are:

```text
METADATA_DATABASE=postgres
METADATA_DATABASE_HOST=postgres-rw.state.svc
METADATA_DATABASE_PORT=5432
METADATA_DATABASE_NAME=periplus_lake
METADATA_DATABASE_SCHEMA=ducklake
MAINTAIN_LAKES=ducklake
MAINTAIN_ALL_LAKES=false
CATALOG_STORAGE=s3-compatible
CATALOG_STORAGE_ENDPOINT=http://192.168.40.101:7070
CATALOG_STORAGE_REGION=us-east-1
CATALOG_STORAGE_BUCKET=periplus
ORPHAN_CLEANUP_ENABLED=false
DUCKDB_THREADS=1
DUCKDB_MEMORY=256MB
```

Supply `METADATA_DATABASE_USERNAME`, `METADATA_DATABASE_PASSWORD`,
`CATALOG_STORAGE_ACCESS_KEY_ID` and `CATALOG_STORAGE_SECRET_ACCESS_KEY` from
platform Secrets. Verify the discovered data root is exactly `ducklake/`, never
the shared bucket root containing `repository/`. Inspect persisted DuckLake
`expire_older_than` and `delete_older_than` policies before starting mutation;
disabling orphan cleanup alone does not disable snapshot/file expiry. If the
selected maintenance release cannot preserve the required recovery window, leave
it stopped until the policy is resolved.

Compaction alone does not supply backups. Periplus retention stays disabled for
initial acceptance; [RETENTION.md](RETENTION.md) defines its separate ownership.

Use a quiesced recovery point initially; independently timed database and object
backups are not an established consistent recovery set:

1. Block new public/admin mutations, pause crawler dispatch, drain remote browser
   sessions and pending ingestion/materialization, then stop all Periplus writers
   and LakeDucktor. Stop queries too before destructive restore/cleanup. Keep Flux
   from restarting writers during the operation. Verify no writer transactions or
   object operations remain; include the bounded remote-transaction/claim windows
   in [retention](RETENTION.md).
2. While quiesced, capture both control and metadata databases, raw and lake object
   trees (including object metadata/xattrs), Versity IAM/configuration, and the
   Periplus NATS account including consumers/KV. Record the recovery-set identity,
   exact images/chart, database recovery point and object snapshot identifiers.
   A database dump or WAL recovery point alone is insufficient.
3. Restore into isolated databases, object roots and NATS account using the same
   recovery set and application revision. Prevent all contact with original writer
   services. Restore identities/grants through the platform and verify object reads
   before starting writers. Never attach restored metadata to partially restored
   objects or restore control state independently of retirement tombstones.
4. Start the matching application privately, allow expired leases/claims to recover,
   check catalogue/query reads and receipt/backlog progress, then run a small crawl.
   Confirm no duplicate logical evidence and no missing referenced objects. Only
   switch production endpoints after validating this isolated recovery.
5. Establish the backup window and maximum reader/rebuild/CDC lifetimes before
   enabling snapshot expiry or orphan cleanup. Cleanup must preserve files needed
   by supported restore points and pinned snapshots. No numeric maintenance horizon
   is approved by this example. Retain the recovery set until the drill succeeds.

## Access and acceptance

Route public to `periplus-public:80` and admin to `periplus-admin:80`, on different
TLS hostnames. Protect the entire admin hostname with Cloudflare Access, including
all `/api` paths and WebSockets. Restrict origin ingress to that authenticated
path. Core `periplus-api:8000` and `periplus-query:8000` stay private. Permit required
query-to-core access for execution limits and crawler-to-query corpus selection.
The `applications` namespace is already allowed by Stolosio's CDP NetworkPolicy.

Public intentionally proxies restricted collection submission/status and read-only
queries without operator Access. Its token cannot start crawls. Apply aggregate
rate/body limits to public ingress and block public `/api/metrics`; internal
monitoring uses its service credential. Never expose an unrestricted core proxy.
Admin SQL is writable and belongs only behind the admin route.

Before public launch, record these results against the pinned release:

1. Setup completes; eight Deployments are rendered; with crawler enabled all roles
   become ready. No missing Secrets, rejected NKey authentication or stream-limit
   failures. Metrics/logs identify each role.
2. With dispatch capped at two, submit one owned/test URL with page limit one and
   no traversal. Approve/start through admin. Confirm capture, immutable raw object,
   ingestion, materialization and a public query returning the captured evidence.
3. Repeat with a small backlog and terminate one busy worker gracefully. Confirm
   redelivery/recovery drains work without duplicate logical evidence. This is a
   worker-restart check, not a total-account restore test.
4. Complete reader-denial tests and the isolated coordinated restore drill. Record
   a NATS leader-loss check separately from full application recovery.
5. Unauthenticated admin page and API requests are challenged; origin bypass fails.
   Public reads work, privileged public requests fail, and core/query/metrics cannot
   be reached through public ingress. Verify canonical URLs, robots and sitemap on
   the chosen public origin. Measure browser occupancy and database connections.

Repository lint/render checks cannot establish live grants, recovery or public
routing. Complete those acceptance steps during homelab onboarding before calling
this deployment ready for valuable data.

## Local validation (2026-09-09)

The example passed Helm lint and rendering. Helm contract tests passed, including operational replica propagation and invalid
replica bounds (PromQL execution skipped because promtool was absent).
In a disposable PostgreSQL 17 container, the supplied grants permitted reads of
both existing and subsequently created tables. INSERT, UPDATE, DELETE, TRUNCATE,
CREATE in the metadata schema and SET ROLE to the writer were denied even after
turning transaction read-only off. In disposable VersityGW v1.8.0, the supplied
policy allowed lake GET and denied PUT, DELETE, repository GET and ListBucket.
Both containers were removed. These checks did not exercise registered Parquet
queries, homelab PostgreSQL 18, NATS restore, LakeDucktor or live cluster routing.

A disposable three-server NATS 2.11 cluster was provisioned through the application
startup functions with operational replicas set to three. After abrupt loss of
the crawl-stream leader, all four coordination buckets retained their probe
values and accepted compare-and-set updates. Crawl messages published before and
after failure were consumed and acknowledged after leader election. Consumer
lookup briefly timed out during election. The test containers/network were
removed. This exercises NATS replication, not full application/database/object
recovery or physical homelab host failure.
