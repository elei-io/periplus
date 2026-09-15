# First ClickHouse E2E result

The requested first `capture → raw storage → NATS → ClickHouse → materialization
→ public query` flow is implemented and verified on `codex/clickhouse-experiment`.
This is an experiment checkpoint, not acceptance of the complete replacement plan.

## Evidence audit

| Requirement | Current evidence |
| --- | --- |
| Isolated experiment from origin/main | `codex/clickhouse-experiment`, based on `5e7c572`, in the separate periplus-clickhouse worktree; changes remain uncommitted |
| Local Compose setup | Pinned ClickHouse image, control Postgres, JetStream and raw S3 storage; all five backend roles healthy |
| Empty-volume reproducibility | Local Compose volumes removed and recreated; setup and full smoke passed, recorded under Clean-volume reproduction in PROGRESS.md |
| Homelab acquisition | Standard CDP at ws://192.168.40.21/v1/connect; fresh example.com collections use Stolosio with reuse disabled |
| Immutable raw evidence | Independent repository verification matched full SHA-256, 559 content bytes and 399 stored bytes to the ClickHouse visit |
| Durable delivery | FILE-backed JetStream; ingestor and materializer independently consume visit evidence; downtime test allowed base ingestion while withholding public readiness |
| ClickHouse ingestion | Exact receipt/digest replay and conflict tests; concurrent consumers create one row; lost insert response reconciles durable evidence |
| Materialization | Shared HTML parse and visit-owned links; partial output and missing base evidence stay hidden; real process death after commit and before ACK redelivers and reconciles without duplication |
| Public query | Restricted native query service; actual HTTP result joins capture, heading and link; SDK buffered/streamed results verified against real ClickHouse |
| Query lifecycle | Server-enforced privileges/resource limits; admission and safe error handling; CPU-bound connection cancellation verified against system.processes; service reuse succeeds |
| Required checks | make check exit 0: 782 backend tests (43 skipped), 24 SDK tests (six skipped), shared package checks/tests, public and admin checks/builds; opt-in ClickHouse/JetStream tests run separately |

Latest image smoke: collection `7b7539f5-601d-49a6-a2bd-6df6b8ed1141`, capture
`0a3ea3f1-70bb-4029-8d04-7437a157c67f`, query
`77d13d11-395c-4683-a30d-8babe8aa7cb8`. The complete result was
`[capture_id, "Example Domain", "https://iana.org/domains/example"]`.
Readiness was observed after 16.67 seconds, a smoke observation rather than a
percentile or capacity claim.

## Reproduce

With the experiment's local credentials and authorized CDP endpoint configured:

```sh
docker compose build periplus-setup
docker compose up -d --wait periplus-api periplus-query periplus-crawler periplus-ingestor periplus-materializer
uv run --project packages/periplus python scripts/clickhouse_smoke.py --query-url http://127.0.0.1:8010
uv run --project packages/periplus python scripts/clickhouse_recovery_smoke.py
make check
PERIPLUS_TEST_CLICKHOUSE=1 uv run --project packages/periplus python -m unittest discover -s packages/periplus/tests -p 'test_clickhouse_*integration.py'
```

Integration fixtures write disposable evidence and may leave raw references to
removed temporary fixture directories. Use only local disposable stores. The
recovery smoke temporarily stops the materializer and restarts it in finally.

## Full-replacement work remains

EXIT_CRITERIA.md remains the acceptance contract for the larger migration. This
first-flow result does not satisfy that entire document. Remaining work includes
complete capture-envelope/header/redirect limits and completeness semantics,
background rebuild/activation, permanent-failure operator workflows, the retention
and raw-reader protection protocol, removing old KV job copies and unused lake
modules, converting unported API/UI capabilities (including search and admin SQL),
canonical documentation, backup restoration and measured homelab capacity.

Destructive retention is disabled. The old janitor is not deployed by this Compose
experiment. No production Periplus data was migrated or deleted. No billion-content,
200 TB, p99 freshness, or production-readiness claim follows from this checkpoint.
