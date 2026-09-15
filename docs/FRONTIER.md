# Shared frontier

Postgres owns collection intent and current execution. The crawler is the sole
page-acquisition primitive; manual and scheduled requests both admit URL interests
into the same frontier. Imports expand the raw corpus separately and never trigger
unbounded traversal of imported links.

## Intent and sharing

A collection freezes seed URLs/description/corpus SQL, page-local follow SQL,
maximum depth, page/duration budgets, selection context and request class.
Collection-local URL deduplication prevents repeated budget consumption. Compatible
pending interests share an acquisition; dispatch freezes participants. Each
collection retains independent traversal, budgets and a `collection_results`
association to the resulting capture. Later eligible reuse creates another
business association, not another physical capture or a retroactive capture cause.

Postgres domain/content policies control behavior. One standard CDP endpoint owns
browser transport and capacity. Per-domain NATS permits enforce concurrency/pacing
across replicas. These permits are separate from operation leases and from local
client limits. A denied dependency/permit does not consume a physical attempt.
Uncertain started attempts retain accounting and are not silently charged twice.

## Execution

The transactional outbox publishes acquisition/selection work and frozen capture
records. Outbox leases fence competing publishers. A crawler stores immutable
payload bytes and commits its operational result before waiting for archive
publication. The relay commits the standalone raw capture and verifies the returned
identity/digest before marking result associations archived. A missing NATS hint
cannot hide committed archive evidence from materializers.

Collection traversal does not wait for ClickHouse. Follow SQL reads only the
current page's bounded navigation package with standalone DuckDB; corpus seed SQL
uses the isolated ClickHouse query service. The API validates and adapts requests;
domain transitions belong in `crawl/runtime/`.

Pause stops new collection work; started acquisitions retain their completion and
accounting rules. Cancellation stops remaining demand without deleting captures
already produced. Collection settlement, archive publication and query readiness
are distinct states and are reported separately.

## Retention and visibility

Completed frontier rows and navigation packages are temporary operational state.
Janitor cleanup preserves collections, their accounting and `collection_results`.
History, arrivals and reverse collection provenance come from those Postgres
records, with bounded ClickHouse lookups for capture facts/readiness.
Imported captures may have no collection association; their source provenance is
in the raw envelope and material corpus.

Reuse cannot select a capture with an accepted retirement intent. Explicit
capture retirement checks unfinished/unexpired collection protection under the
frontier admission lock before scheduling its archived tombstone. See
[RETENTION.md](RETENTION.md).

Live attempt rates count distinct physical start times in retained operational
attempt records, including retries and uncertain starts. These short windows are
not permanent analytical attempt-history tables. Capture facts belong to the raw
archive and can be projected for analytical use; customer intent remains Postgres.

Behavioral tests cover shared acquisitions, independent budgets, failed/uncertain
attempts, dependency deferral, cancellation, outbox fencing and bounded cleanup.
Exercise one low-depth URL with low concurrency when changing the capture path.
