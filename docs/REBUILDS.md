# ClickHouse rebuilds

The ClickHouse experiment runs in this MacBook's Docker Compose. The crawler uses
the configured homelab CDP endpoint; no other production service is a test target.

## Operator workflow

Open the admin application's **Data → Materialization** page. Start one candidate
with a bounded page size (1–128 visits; default 32). Queries continue using the
serving target. Pause affects historical work only; live delivery continues.

A candidate moves through preparing, building, verifying and ready. Inspect its
partition ranges, verified visit count, last durable scan key, live pending count,
barrier and acknowledgment floors. A blocked build displays its failure class and,
for material input failures, its visit identity. Repair the cause and choose Retry.
Infrastructure/claim contention retries automatically after a bounded delay.

Choose Activate only after the worker reports readiness and live catch-up. This
changes one Postgres publication pointer. Every query captures its binding through
the internal execution-context API before executing; all relations in that query
use the same immutable query namespace. This does not freeze incoming crawl data.
The existing execution-policy request also supplies the binding, so there is no
second manifest polling system or public parser-generation parameter.

Cancel fences checkpoints immediately. The controller waits 610 seconds, exceeding
the bounded writer/remote-operation window, before deleting the candidate consumer
and releasing its source protection. The UI shows the drain deadline. Candidate
files are not deleted by cancellation. Killing the worker does not shorten this
protection interval. A replacement operation owner fences the previous owner's
checkpoints before resuming.

## What owns what

- Postgres: build intent/status, one range per source month, verified range cursor,
  and the selected publication. There is no per-document success table.
- ClickHouse: immutable ingestion evidence and target-specific derived output.
- JetStream: retained and future ingestion delivery, including typed catch-up
  barriers. Every target has its own durable `DeliverAll` consumer.
- Raw repository: immutable content-addressed bytes shared across targets.

The materializer process runs one elected operation owner with one live execution
lane and one independent historical lane. Additional replicas are failover owners;
this implementation does not claim parallel historical throughput from replicas.
The range checkpoint itself is sufficient delivery for this bounded historical
lane; no second backfill queue or permanent per-page notification ledger is added.

Range planning reads active partition metadata and the last sorting key in each
month. Pages follow `(requested_url, finished_at, visit_id)` without OFFSET or a
corpus-sized ID plan. Content is parsed only when missing in the target; stored DOM
structure supplies relative-link resolution for subsequent visits. Historical
inserts are batched and verified before advancing the cursor.

The candidate consumer exists before scanning. It covers retained events, future
arrivals, and delayed base commits behind completed scan positions. Live visits
are acknowledged only after complete material output and matching base evidence
are visible. Unresolved ingestion failures remain pending; dead letters also block
readiness. A named barrier and both contiguous ACK floors bound catch-up.
Stream/consumer replacement invalidates readiness; retry never recreates a recorded
consumer to conceal lost coverage.

## Publication and retained targets

The active and most recent previous target continue receiving live data. On the
next activation an older previous target drains and its consumer is retired.
Physical retired targets and query grants remain retained. Automatic physical
reclamation, rollback controls and distributed reader reclamation are not enabled.
This conservative policy prevents dropping tables beneath old in-flight readers.
Raw destructive retention remains disabled.

Targets pin `html-links-v1` semantics. This release rebuilds that family; introducing
a different parser semantic version requires an explicit worker-version rollout.
It does not silently reinterpret an existing target using changed semantics.

## Local checks

```sh
docker compose build periplus-setup periplus-admin periplus-public
docker compose up -d --wait periplus-api periplus-query periplus-crawler periplus-ingestor periplus-materializer periplus-admin periplus-public
uv run --project packages/periplus python scripts/clickhouse_smoke.py --query-url http://127.0.0.1:8010
# Run the capture smoke three times on a fresh corpus before the rebuild smoke.
uv run --project packages/periplus python scripts/clickhouse_rebuild_smoke.py
make check
PERIPLUS_TEST_CLICKHOUSE=1 uv run --project packages/periplus python -m unittest discover -s packages/periplus/tests -p 'test_clickhouse_rebuild_storage_integration.py'
```

The rebuild smoke uses the actual HTTP APIs, pauses history while a fresh crawl
arrives, stops/restarts the Compose materializer, verifies exact source/target
coverage, activates, and exercises public SQL including CTE shadowing. It refuses
non-local database/NATS endpoints. The storage integration test uses a disposable
private target and removes it afterwards.

Admin: <http://localhost:8081/data/materialization>. Public SQL:
<http://localhost:8080/sql>. Storage reports active ClickHouse parts, disks, merges,
Postgres relations and JetStream usage. Raw storage inventory is explicitly unknown,
not presented as zero. Raw downloads verify bytes before HTTP success and use bounded
temporary spooling. Ingestion failures can be inspected and retried from Data →
Ingestion; request paths reuse startup-owned NATS handles.
