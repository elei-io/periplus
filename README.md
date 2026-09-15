# Periplus

Query observed websites with SQL. Periplus acquires pages through a standard CDP
service, imports supported Common Crawl records, preserves an immutable raw
archive, and builds a queryable ClickHouse corpus.

```sql
SELECT capture_id, page_url, effective_url
FROM public_v1.capture
LIMIT 10;
```

The raw archive is the corpus authority. Postgres holds customer requests,
collection results and operations. NATS delivers work. ClickHouse stores derived
documents, captures and `public_v1` views; it can be rebuilt from the archive
without the original databases or queues. [Architecture](docs/ARCHITECTURE.md)
and [schemas](docs/SCHEMA.md) explain the boundaries.

## Local development

Use Python 3.14 with `uv`, Node.js 24+, Docker Compose and an externally supplied
CDP endpoint. Copy `.env.example` to `.env`, configure CDP and generate three
distinct API service tokens using `openssl rand -hex 32`.

```sh
make sync
make check
make compose-up
docker compose up -d --scale periplus-ingestor=2
```

Public application: http://localhost:8080. Admin: http://localhost:8081.
The local setup retains the configured homelab CDP service; it does not deploy
this storage cutover to homelab production. See [deployment](docs/DEPLOYMENT.md).
Admin credentials stay in its server-side gateway; public queries use a separate
read-only ClickHouse account. Neither frontend connects directly to a database.

The Python backend in `packages/periplus` supplies API, query, crawler, archive
shared ingestor, setup and janitor roles. `periplus-admin` is the operator
Vite app; `periplus-public` is the public Next.js app. Console, shell and SDK
packages are HTTP clients. Use [the Python SDK](packages/periplus-python-sdk/README.md)
or `npm run periplus -- 'SELECT capture_id FROM public_v1.capture LIMIT 10'`.

## Operational guides

- [Capture lifecycle](docs/LIFECYCLE.md)
- [Rebuild controls and archive-only recovery](docs/REBUILDS.md)
- [Raw storage and backups](docs/STORAGE.md)
- [Retirement and janitor responsibilities](docs/RETENTION.md)
- [Common Crawl imports](docs/COMMON_CRAWL.md)
- [Query contract](docs/QUERY.md)
- [Validation evidence and limits](docs/VALIDATION.md)

Research preview: public submissions and their details are shared. Collection
settlement and query readiness are separate milestones. Coverage, freshness and
retention follow deployment policy. Private query history retains SQL/parameters
for up to 30 days and does not store result data.

Copyright (c) 2026 Ekku Leivonen (elei.io). Platform: AGPL-3.0-only; standalone
Python SDK: Apache-2.0. See [LICENSING.md](LICENSING.md),
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
