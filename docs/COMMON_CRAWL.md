# Common Crawl corpus imports

Common Crawl is an operator-controlled corpus expansion source. Collections use
Periplus's browser crawler. There is no collection crawler selector, archive
fallback, or automatic traversal from imported links.

## Admin workflow

Open **Data → Archive imports** (`http://localhost:8081/data/archive-imports` in
local Compose). Submit a pinned Common Crawl dataset, inclusive UTC capture window,
1–100 explicit HTTP(S) URLs, and an archive-download byte allowance. The URL list
is the record bound: at most one selected capture per normalized URL. Wildcards,
domain discovery, and automatic recurring imports are outside this first version.

Each job shows URL results, original capture times, capture IDs, already-archived
captures, referenced unique compressed HTML size, and consumed download allowance.
Referenced bytes can already exist elsewhere in the corpus; they are not an
estimate of incremental disk usage. Content-addressed storage shares identical
HTML across observations. No native browser attempts or collection records are
created by an import.

The job states are queued, running, blocked, completed, and cancelled. Missing,
unsupported, and retired captures are explicit per-URL outcomes. Transport,
validation, storage, and ingestion-delivery failures block the job with a visible
error. Retry resumes its checkpoint. Cancel stops remaining work; an in-flight
archive write or delivery can finish. Cancellation does not delete evidence.

A completed job means every URL was processed and supported evidence was archived
and published for ingestion. It does **not** prove materialization/query readiness;
use the existing ingestion/materialization status and public SQL to check that.

## Ownership and recovery

`archive_imports` in control Postgres holds bounded operator intent, selected record,
progress, and diagnostic results. It is not the capture authority. Existing ingestor
replicas service these control jobs; a provider-scoped renewable NATS operation
lease allows one Common Crawl import lane at a time. No new service or delivery
queue is added. Published evidence uses the existing JetStream ingestion lane.

The worker checkpoints selection before fetching, reserves declared WARC range bytes
before **every download attempt**, stores verified HTML and immutable evidence in S3,
then checkpoints the archive key before publishing. Database transactions never span
remote I/O. Revision checks prevent stale workers from overwriting cancellation or
newer progress. Retries replay stable capture identities through ordinary ingestion.
The import control loop is visible in ingestor subsystem health.

The byte allowance counts potential WARC range downloads, including failed attempts
and uncertain retries. CDX lookup overhead is excluded. An exhausted allowance
blocks further downloads; cancel that job and explicitly submit remaining URLs with
a suitable budget. A failure after S3 publication but before its checkpoint may
require another range download. Once the archive key is checkpointed, publication
retry reads S3 without contacting CC.

Raw storage retains independently deletable, content-addressed Zstd HTML and
immutable envelopes under:

```
raw/v1/evidence/common-crawl/<dataset>/<prefix>/visit-<capture-id>.json.zst
raw/v1/evidence/periplus/native/<prefix>/<evidence-id>.json.zst
```

Imported envelopes preserve original WARC identity, location, exact WARC and HTTP
headers, capture timestamp, and document identity. Bodies are `response_body`, not
browser renderings. Ingestors also journal new native evidence. Importing later
never changes the original capture time. New envelopes can reconstruct evidence
without querying the original Postgres or ClickHouse; rebuilding a whole installation
still needs operational state and retirement decisions. Older captures were not
backfilled. Full S3-only disaster recovery and safe corpus deletion remain separate
work; destructive retention stays disabled in the local experiment.

## HTTP and CLI

Admin-only routes:

- `POST /operations/archive-imports` — `{id, specification}`; same identity and intent is idempotent.
- `GET /operations/archive-imports` — latest 50 jobs.
- `GET /operations/archive-imports/{id}` — one job.
- `POST /operations/archive-imports/{id}/retry` — resume a blocked job.
- `POST /operations/archive-imports/{id}/cancel` — stop remaining work.

Specification example:

```json
{
  "dataset": "CC-MAIN-2026-34",
  "urls": ["https://example.org/"],
  "captured_from": "2026-08-01T00:00:00Z",
  "captured_until": "2026-09-01T00:00:00Z",
  "max_download_bytes": 67108864
}
```

The existing `periplus-archive import --manifest <file> --limit 1000` command remains
a bounded, foreground import of preselected CC index records. `lookup --dataset ...
--url ... --max-age-days ...` is a foreground diagnostic import. These commands do
not create admin jobs. `replay --prefix raw/v1/evidence/... --limit 1000` republishes
archived evidence through the normal worker lane. CLI output and failures are
owned by the invoking operator; admin jobs are preferred for background work.

## Supported records and current limits

Only complete HTTP 200 UTF-8 `text/html` WARC response records are imported.
Truncated/segmented records, revisits, non-UTF-8 HTML, and HTTP content/transfer
encodings other than identity are rejected. Outer gzip WARC compression is supported.
Limits are 8 MiB compressed range, 32 MiB expanded record, and 64 KiB header blocks.
Exact range responses, lengths, index URL/time, and available WARC digests are verified.

The CDX lookup inspects a bounded set (up to 20) of provider candidates and chooses
the newest exact Periplus URL match within the requested window. It does not promise
an exhaustive search of every historical capture. Index requests are paced; adding
ingestor replicas does not multiply the admin import lane. Bulk columnar discovery,
range coalescing, broad WARC support, and rendered-quality guarantees are not included.

## Local deployment and verification

Apply Alembic `20260915_0022`, then redeploy API, ingestor, crawler, admin, and public
together for the removed collection contract. No new environment variables, containers,
or projection rebuild are required. The previous experiment's disposable collection
intent contains removed fields; remove those operational test fields before running
new workers. Do not rewrite immutable historical envelopes as part of deployment.

Run `scripts/common_crawl_smoke.py` from the backend's `uv` environment for real CC
admin import, duplicate replay, miss, byte-budget, cancellation, access-control,
public SQL, and exact-byte verification. `scripts/common_crawl_recovery_smoke.py`
exercises process death after archival publication, raw replay, and rebuild activation.
Targeted tests cover checkpoint recovery, cancellation fencing, failed publication,
unsupported/missing records, and retry download budgets. Run `make check` as well.
