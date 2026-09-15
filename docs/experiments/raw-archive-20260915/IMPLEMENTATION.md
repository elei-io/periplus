# Initial collection-source experiment — 2026-09-15

**Historical proof:** the collection-source entry point below has been removed.
The current implementation uses separate admin import jobs; see
[ADMIN_IMPORTS.md](ADMIN_IMPORTS.md) and [the current runbook](../../COMMON_CRAWL.md).

Implemented in the `codex/clickhouse-experiment` worktree.
Local Compose volumes were preserved. Production Periplus stores and deployment
configuration were not changed. The native regression used the already configured
homelab CDP service.

## Delivered behavior

- Collection requests explicitly choose `periplus` or `common_crawl` in both
  request forms and the existing API. Archive requests never silently fall back
  to a browser. Optional dataset and capture-age bounds are frozen request intent.
- Common Crawl responses preserve original capture time, exact UTF-8 body, WARC
  identity/location/digest, and exact WARC/HTTP header bytes, including duplicate
  headers. They contain no invented browser attempts or native content policy.
- Stable archive capture IDs and the existing content-addressed repository make
  repeated imports and shared collection results idempotent.
- Archived results use ordinary collection budgets, URL deduplication, navigation,
  follow SQL, lineage, outbox, NATS ingestion, ClickHouse materialization and public
  queries. Misses are failed operational URL results with no fictional visit.
- A bounded manifest importer supports operator-selected initial corpus inputs.
  A raw-journal replay command repairs missing delivery after an archival commit.
- Native ingestion now journals verified evidence and immutable lineage before
  ClickHouse insertion. This does not backfill older local captures.

The complete contract, commands and limits are in [COMMON_CRAWL.md](../../COMMON_CRAWL.md).

## Real Compose observations

`scripts/common_crawl_smoke.py` imported the latest matching Example Domain record
from CC-MAIN-2026-34:

- Capture `0326c33f-d03c-55bc-b9e2-d9751db3b710`.
- Original observation `2026-08-20T00:24:01Z`, not the September import time.
- 559 exact raw bytes, SHA-256
  `ff67a9d764d6a2367a187734e697f6a53217db9a21c101d410a113ca871a299d`.
- The public query returned `Example Domain` and
  `https://iana.org/domains/example`.
- Readiness was observed after 14.77 seconds on the idle local stack. This is one
  smoke observation, not an ingestion latency percentile or capacity measurement.
- Two collections shared exactly one stored visit, with zero native attempts.
- An age limit of one day reported `common_crawl_no_match` and zero supplied pages.
- A depth-one request consumed two page units, imported the parent and reported
  the linked URL's missing eligible CC response. This proves the recursive source
  choice and miss accounting, not two successfully imported linked pages.

The admin form was also used through the browser to submit collection
`cc7721cc-0c6a-4ec1-8156-7b1bdb068ade`, choosing Common Crawl and leaving dataset
selection at its default. The visible result settled with one supplied page,
confirmed lineage and verified query readiness. Its final intent display names
Common Crawl and the archive age bound.

## Failure and rebuild proof

`scripts/common_crawl_recovery_smoke.py` downloaded a real IANA WARC response,
committed its body/envelope, then exited its importing process using `os._exit(31)`
before any NATS publication. The capture was not publicly visible at that point.
The raw replay command subsequently delivered it to the ordinary workers.

- Capture `47502c34-df36-500a-b7b7-878be23a4833`.
- Original observation `2026-08-09T10:51:11Z`.
- Public heading `IANA-managed Reserved Domains`.
- Rebuild `82895c9c-bd7b-4b90-b5ca-756ddc8e1895` caught up and activated; the public
  capture/time/heading row remained exactly equal afterward.

An earlier Example Domain recovery run reached historical readiness but attempted
activation before its live delivery floor had settled. The server correctly
returned 409. The smoke now waits for zero live pending work and both persisted
floors to reach the barrier. The earlier build activated after those conditions
were satisfied; it was not discarded or forced through the gate.

A separate read decoded all three imported raw envelopes with Postgres,
ClickHouse and NATS endpoints deliberately pointed at unreachable localhost
addresses. S3 alone supplied their typed capture evidence. This is not a full
empty-database restore drill or a destructive-retention proof.

The manifest CLI successfully reimported the earlier Example Domain WARC response
as capture `f99632e1-70cd-5e27-a1f2-7dcafa622acb`. It shares the exact same body as
the August 20 capture but retains its distinct August 7 observation identity.

A fresh native browser regression subsequently returned the expected heading/link
through public HTTP SQL for capture `c72ea67a-f82d-4fc4-85dd-f64bde64317d`, with
query readiness observed after 16.55 seconds.

## Checks and remaining scope

Targeted tests cover exact raw/metadata recovery, cross-URL deduplication,
conflicting identity rejection, retirement exclusion, unsupported representations,
WARC payload digest corruption, index/record mismatch, ignored HTTP ranges,
ClickHouse evidence reconstruction, retry timestamps, and shared/missing
collection accounting. Full `make check`, admin lint and admin tests are run for
this implementation; generated logs remain under ignored
`.artifacts/raw-archive-20260915/`.

Supported inputs are deliberately limited to complete UTF-8 HTTP 200 HTML with
identity HTTP encodings. General revisit resolution, other encodings, dense WARC
streaming, bulk columnar-index planning, metadata packing, full disaster recovery,
durable S3 eviction tombstones, safe destructive retention and billion-object
capacity remain outside this local acceptance.
