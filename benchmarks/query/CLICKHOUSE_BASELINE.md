# Recovered business SQL: ClickHouse baseline

The original workload is back in `cases/`, unchanged from
`codex/query-business-case-optimizations` at
`56e2610f7145a45fac3d70a0c2722854e9079da5`. It contains 24 cases, including
experimental layout diagnostics as well as product questions. The former
DuckDB classifications and budgets are historical evidence, not current
ClickHouse conclusions. Two added cases reproduce the reported element count
and ten-row preview. A separately named `single-capture-links-public` changes
only the original case's `experimental` namespace to `public_v1`.

## Completed campaign — 2026-09-15 UTC

**Of the 24 original cases: four executed, ten reached memory/deadline limits,
and ten need missing features or explicit SQL ports.** Three executed cases
returned no rows. The fourth returned one domain aggregate, which is thin
coverage rather than a representative inventory benchmark. The added
namespace-only selected-links port returned 50 correct rows. Both added user
reproductions still fail their existing resource budgets.

The campaign ran at approximately 16:47–16:48 UTC against published build
`2a70e436-37aa-42a5-932e-3caa2654d889`, publication revision 1, recipe
`8588725c70c80a07e6f5580b2146094b1fc60ce7388396d18c84a058a01766f6`.
The deployed image revision was `e615dec93d2246dca7d8aa0582cdc798459b55e8`.
All logged campaign SQL used that build's query database. Coverage checks before
publication found 229,024 observations with unique capture IDs, 185,191 complete
captures, 176,750 unique document/content IDs and 279,499,131 elements. Complete
captures had no missing documents; all journal cursors were caught up with zero
outstanding or failed batches. The main material tables occupied approximately
18.90 GB compressed in active parts at the snapshot, excluding the previous
target, caches, logs, backups and raw archive.

Rows below show the warm execution for completed queries and the single attempt
for rejected queries. They are individual measurements, not latency percentiles.
CH 241 = memory, 307 = scanned bytes, 159 = deadline. The normal API policy in
this run allowed 20 seconds, below the server's 45-second maximum. Missing
interfaces and the two `hash()` preparation errors are listed separately below;
they have no result-execution performance measurement.

| Case | Outcome | API ms | Server ms | Rows read | Read MiB | Recorded memory MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `append-only-index` | CH 241 | 245 | 54 | 229,049 | 33.0 | 294.8 |
| `books-heading-search` | CH 159 | 20484 | 20275 | 682,137 | 937.3 | 369.1 |
| `current-domain-inventory` | 1 row | 217 | 14 | 177,328 | 5.5 | 13.3 |
| `element-text` | empty | 339 | 67 | 405,774 | 12.9 | 244.3 |
| `exact-page-history` | empty | 199 | 7 | 0 | 0.0 | 7.5 |
| `exp-selected-headings` | CH 241 | 407 | 123 | 229,067 | 44.8 | 399.7 |
| `exp-selected-headings-ssa` | CH 241 | 323 | 119 | 229,098 | 54.9 | 416.8 |
| `html-element-count` | CH 307 | 768 | 582 | 230,939 | 1040.5 | 343.8 |
| `html-element-preview` | CH 241 | 216 | 49 | 229,049 | 33.0 | 294.9 |
| `index-text-equality` | CH 241 | 261 | 41 | 229,049 | 33.0 | 294.8 |
| `index-text-equality-bounded` | CH 241 | 271 | 91 | 229,144 | 41.8 | 430.0 |
| `index-text-equality-quarter` | CH 241 | 249 | 43 | 229,049 | 33.0 | 283.7 |
| `python-external-destinations` | CH 241 | 935 | 640 | 601,707 | 364.1 | 510.7 |
| `recent-ingestion-activity` | empty | 252 | 5 | 0 | 0.0 | 7.5 |
| `selected-content-headings` | CH 241 | 382 | 111 | 229,098 | 54.9 | 416.8 |
| `single-capture-links-public` | 50 rows | 217 | 51 | 812,123 | 26.6 | 77.5 |
| `single-capture-subtree` | CH 241 | 284 | 84 | 634,823 | 49.3 | 326.3 |

The selected-links initial run took 228 ms API / 63 ms server and read 1,040,554
rows / 45.6 MiB. Its warm run still selected 22,831 of 23,251 available marks.
Those are broad reads, not an isolated one-page lookup. Most bytes came from
the NVMe filesystem cache, so its speed does not establish cold NAS performance.
Both ordered answers exactly matched an independent Python `Counter` over the
selected material capture's 59 link occurrences and 52 distinct targets,
sorted by count then URL and limited to 50. The capture ID was
`075dc086-adb8-499c-8796-589ee28136e5`; its document existed exactly once.
This validates the view's aggregation/selection against stored projection data,
not extraction fidelity against original HTML.

The books-heading query reached its 20-second deadline. Profile events record
338 S3 GET operations and about 2.85 GB read from the remote cache source,
including prefetch work, versus 0.98 GB counted as logical query read bytes.
This is evidence of significant remote I/O for that execution. It does not
separate NAS disks, network and request latency well enough to label the HDDs
the bottleneck. First address why the query reads unrelated document data.

Text reconstruction failures are classified as both layout and evaluation
behavior. The broad capture-membership/element access paths are schema/layout
issues with optimizer pushdown still to isolate. The Python external-links
case exhausts memory while reading the `links` array, not the subtree-text
function. Its exact planner contribution and the books query's I/O latency
breakdown remain provisional; these measurements do not justify blaming a
single ClickHouse optimizer defect or changing hardware yet.

The empty exact-page and recent-capture queries initially read 229,024 rows;
their warm executions read none. Cache/filter effects must not be mistaken for
an indexed positive lookup. The impossible `fixture` ID still caused 405,774
physical rows of work and roughly 244 MiB allocated memory on its warm run.

Local detailed evidence is intentionally untracked under
`.artifacts/query-clickhouse/`: `full-baseline.jsonl`,
`full-baseline-query-log.jsonl`, `full-metrics.json`, `full-build.json`,
`full-storage.jsonl`, and `links-independent.json`. The report preserves the
material conclusions; the runner and original SQL are versioned. Reproduce:

```sh
python3 benchmarks/query/run.py --case all \
  --endpoint https://periplus.dev/api/query \
  --output .artifacts/query-clickhouse/next-baseline.jsonl
```

Keep the same publication and limits for comparison; record any subsequent
storage, view, recipe, API policy or corpus changes explicitly.

### Exact admin reproductions, measured separately

At 16:52–16:53 UTC the admin `SELECT COUNT(*) FROM public_v1.html_element`
reached its 45-second deadline (HTTP 408, CH 159), after 397,646 physical rows
and **94,441,802,970 logical bytes read**. The logged memory was 550,198,497
bytes, with 365 S3 GETs, 8,831,581,759 remote-source bytes and 4,093,681,449
cache-read bytes. Query ID: `5163e099-c082-44b1-bcdf-1c7b9483b6ca`.
It did not return a count. The verified 279,499,131 element total above came
from an independent internal `sum(length(elements.node_index))` check, not
from pretending the failed public-view count succeeded.

The exact admin ten-row preview failed in 78 ms through the internal admin
gateway (55 ms server), HTTP 422 / CH 241, attempting a 3.97 GiB allocation
that would bring total query memory to 4.24 GiB against its 4 GiB budget.
Query ID: `cf101b5d-ce38-4a04-9fbc-81acd934b2e6`. The response now says
`Query exceeded its memory budget (ClickHouse code 241).` and no longer warns
about a lost write or rollback for SELECT.

These admin requests used the same complete published target and writer
profile. Their in-cluster gateway timings exclude the user's external network
and Cloudflare path. They are separate from the reader-profile table above.

## What the initial investigation established

The ten-row preview fails before returning ten rows. The native exception names
`substringUTF8(document_text, text_start + 1, text_end - text_start)` during
array expansion: the original partial target attempted a 4.97 GiB allocation.
The completed target's measured public preview attempted 3.97 GiB and would
have used 4.24 GiB in total. The original allocation exceeds both the
public reader's 512 MiB budget and the admin writer's 4 GiB budget. Recorded
memory usage is memory successfully allocated, not that failed allocation.
The public view stores one document text and an array of elements, then
reconstructs each element's subtree text. Its evaluation can perform enormous
intermediate work before the outer LIMIT. This is **both physical layout and
execution behavior**, not evidence that the HDDs cannot serve ten rows.

A column-isolation check on the partial serving target returned all other
eleven columns for ten elements in 222 ms through the public API; selecting
only `text` still failed with code 241. This isolates subtree-text evaluation
without treating a reduced result schema as a fix.

The count has a different failure in the public API: ClickHouse code 307,
`TOO_MANY_BYTES`, at the reader's 1 GiB scan budget. The plan expands element
arrays and evaluates complete-capture membership; it is not a cheap table-row
metadata count. Admin timing uses different credentials and must not be mixed
with the public baseline. Improving the public view's physical work is required;
raising limits would not prove scalability.

The API previously classified code 307 as storage unavailable, while admin
SELECT errors mentioned uncertain writes and rollback. Commit `e615dec` fixes
those messages without changing SQL, schema, limits or query results. It passed
CI and was deployed through homelab GitOps commit `843f7795`. The deployed
public reproductions now return HTTP 422 with their scan or memory budget.

## Coverage gaps revealed by the unchanged SQL

| Original cases | Current gap |
| --- | --- |
| `jsonld-content-cohort`, `jsonld-declarations`, `latest-capture-jsonld`, `tori-latest-products` | No public `html_jsonld` relation |
| `latest-capture-title`, `metadata-content-cohort` | No public `html_metadata` relation |
| `word-search-captures` | No ranked `public_v1.search(...)` interface |
| `current-page-image-accessibility`, `mdn-main-link-extraction` | Original `hash()` cohort function is unavailable; ClickHouse code 46 |
| `single-capture-links` | Retired `experimental` namespace; a separate namespace-only port is included |

Changing a hash function changes cohort membership. These cases need either a
documented dialect port with an explicitly fixed cohort or restored product
semantics; silently substituting a hash is not a correctness-preserving test.
Likewise JSON-LD/metadata/search gaps are product schema work, not optimizer
failures. Future mixed-representation corpora also require reviewing old
`content_id` joins against the new interpretation-specific `document_id`
contract; the original queries remain available for that review.

## Measurement rules

The runner uses the normal public API sequentially, without settings overrides.
One initial and one warm execution are requested for successful cases. Rejected
or truncated cases are not repeated. Initial does not mean cold: no shared cache
is flushed. API wall time includes transport, preparation and execution;
`system.query_log` separately records server duration, physical rows/bytes and
memory. Logical rows read are not unique documents, and read bytes are not raw
network or disk bytes. Query IDs correlate successful results; failures require
the exact SQL and server start-time correlation because error responses do not
yet return a query ID.

Empty answers are not extraction performance evidence. In particular the
`element-text` case uses the literal ID `fixture`. The retained archive also
contains no MDN or Common Crawl URL observations, so those examples cannot
establish useful positive-result performance. Unordered LIMIT answer membership
need not be stable. Successful nonempty results need independent comparison as
well as timing.

The deployment uses ClickHouse 26.8.2.7. Material data now uses the NAS S3
`corpus` policy with a bounded 256 GiB NVMe cache. Earlier local-NVMe rebuild
timings are not a like-for-like baseline. The reader has two threads, 512 MiB
memory, 1 GiB scanned bytes, ten million scanned rows, and a 45-second maximum;
aggregate reader memory is 4 GiB with eight admitted queries. These bounds stay
in place throughout this investigation.

## Next optimization order

1. Make the unchanged element preview return complete rows within the existing
   memory budget. Measure where array expansion/text reconstruction allocates;
   preserve full subtree text rather than truncating it to make the test pass.
2. Make exact page/capture selection and element access avoid unrelated corpus
   work. Check capture-membership joins, predicate pushdown through views,
   `content_id`/`document_id` lookup paths and element-array reads. Keep the
   original business SQL as the acceptance case.
3. Design accelerated text, metadata and JSON-LD relations around the recovered
   business queries. Restore a small coherent public contract and test positive
   results; do not implement a collection of one-off query rewrites.
4. Repeat with representative positive cohorts, growth in unrelated corpus,
   warm/cold evidence where safely obtainable, and concurrent public traffic
   plus materialization. A fast warm run on this corpus is not a billion-page
   scale claim.
