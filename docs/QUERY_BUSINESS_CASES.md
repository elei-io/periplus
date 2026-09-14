# Production query business cases

Append one numbered case per business use case, including failed executions. Keep the SQL,
measurement table, compact actual plan, result sample and interpretation together here.
Keep every attempt, error and syntax correction under its original case. Never replace
failed SQL with a successful alternative or count a changed selection as completion.
Freeze valid SQL before execution. Do not narrow terms, predicates, joins, scope or
limits, rewrite SQL, or change platform settings to obtain a result. Only obvious
syntax or catalogue-name corrections are allowed; record the original error and exact
correction. Further experiments require an explicit user request.
A timeout or other platform failure on valid catalogue SQL is the case outcome under
the settings in effect. An empty successful result is also an outcome, not a reason
to change the query. Preserve dated runs instead of overwriting measurements.

## Measurement protocol

Use the Python SDK against `https://periplus.dev`, `mode="experimental"`.
Run SQL once; on success, run `EXPLAIN ANALYZE` separately and serially. On failure,
record the exact SQL, error, elapsed client time and any returned metadata, then stop.
Do not rerun a failed query for an analyzed plan; mark unavailable metrics explicitly.
Record each snapshot;
separate requests do not pin a shared transaction. API time includes preparation;
client time includes HTTP and decoding; engine time comes from the separate profile.
These are individual observations, not p50/p95 or cold-cache guarantees. File counts
are reported scan counts; HTTP bytes are transferred bytes, not logical scan volume.
Missing metrics mean unavailable. All examples use read-only public SQL.

```python
from time import perf_counter
from periplus_sdk import Client

with Client("https://periplus.dev", mode="experimental", timeout=180) as client:
    start = perf_counter()
    result = client.execute(sql)
    client_ms = 1000 * (perf_counter() - start)
    profile = client.execute("EXPLAIN ANALYZE " + sql)
    print(result.elapsed_ms, client_ms, result.row_count, result.truncated)
    print(result.source_snapshot, profile.source_snapshot)
    print(profile.rows[0][1])
```

### Cases 03–30 execution protocol

All 28 SQL statements were defined before the first execution and their SHA-256
hashes recorded. No query was narrowed, rewritten or retried. Catalogue discovery
confirmed version 3.0.0 and the experimental namespace. SDK 0.8.0 was loaded from
this checkout. These cases use the SDK default 620-second client timeout so that
server limits can report their own outcome; no platform settings were changed.
The server's numerical resource limits, DuckDB version, memory and spill settings
were not returned by buffered SDK responses and are unavailable in this record.

Each case was prepared once, executed once if preparation succeeded, and profiled
once only after successful execution. Preparation provides an estimated plan even
when execution fails; it is not an actual profile. Preparation and profile times
are separate from the ordinary SQL execution time. Cache state and concurrent
production workload were uncontrolled. An empty result is preserved as a success.
Result samples are at most three rows. Scan counts are emitted rows, not physical
rows examined; HTTP counters are reproduced as reported without assuming cache
scope. No schema or optimizer fixes were attempted. The text plan renderer can abbreviate wide cardinalities and filenames with ellipses;
those are unavailable, not exact values, even when the SDK result is not truncated.
Full returned plans and raw responses are local ignored evidence; each entry retains
the compact plan and measurements. Each scan occurrence is listed separately; sums
of file counts across repeated scans are not counts of unique physical files.

## Campaign overview

30 business cases are recorded: **20 successful answers (including empty answers),
2 query timeouts, and 8 service-unavailable failures**. Of the unavailable cases,
case 23 failed during execution and cases 24–30 failed at preparation; their
hypothesized access paths were not exercised. The service returned 503s in sequence;
this does not establish that case 23 caused the outage. No retries or repairs followed.

| Case | Business use case | Outcome | API execution time | Rows |
| --- | --- | --- | --- | --- |
| 01 | Website health by host | Success; original empty result also retained | 166.35 ms (later variant) | 20 (later variant) |
| 02 | Ransomware recovery shortlist | Query timeout | Unavailable | Unavailable |
| 03 | Agency page audit trail | Success | 130.33 ms | 1 |
| 04 | Daily publishing-monitor coverage | Success | 113.68 ms | 5 |
| 05 | Government redirect destination review | Success | 175.81 ms | 30 |
| 06 | Duplicate-content consolidation | Success | 190.33 ms | 20 |
| 07 | Investor-site content changes | Success | 137.06 ms | 9 |
| 08 | Page-weight budget outliers | Success | 142.93 ms | 20 |
| 09 | Referral sources to a reporting portal | Success | 554.22 ms | 8 |
| 10 | Agency outbound link inventory | Success | 3195.92 ms | 50 |
| 11 | Uncaptured government crawl opportunities | Success | 2077.35 ms | 50 |
| 12 | Two-hop agency discovery | Success (empty) | 3054.03 ms | 0 |
| 13 | Frequently cited destination hosts | Success | 3888.44 ms | 20 |
| 14 | Missing-title SEO audit | Success | 57141.54 ms | 8 |
| 15 | Declared-language market coverage | Query timeout | Unavailable | Unavailable |
| 16 | Open Graph image deployment audit | Success | 7718.18 ms | 30 |
| 17 | Structured-data parse failures | Success | 1013.30 ms | 2 |
| 18 | Product structured-data discovery | Success | 1795.55 ms | 30 |
| 19 | Structured offer price comparison | Success | 331.99 ms | 2 |
| 20 | Known-document heading outline | Success (empty) | 436.73 ms | 0 |
| 21 | Known-document component context | Success | 120.36 ms | 5 |
| 22 | Known-document form content extraction | Success (empty) | 59.92 ms | 0 |
| 23 | Exact consent-copy inventory | 503 at execution | Unavailable | Unavailable |
| 24 | Acquisition-announcement phrase discovery | 503 at preparation | Unavailable | Unavailable |
| 25 | Quantum topic content discovery | 503 at preparation | Unavailable | Unavailable |
| 26 | Climate-finance source coverage | 503 at preparation | Unavailable | Unavailable |
| 27 | HTML component footprint | 503 at preparation | Unavailable | Unavailable |
| 28 | Password fields on HTTP captures | 503 at preparation | Unavailable | Unavailable |
| 29 | Missing image alternative text | 503 at preparation | Unavailable | Unavailable |
| 30 | Linked document-format demand | 503 at preparation | Unavailable | Unavailable |

## 01 — Website health by host

**Use case:** Prioritize website review using latest retained HTML status per URL;
rank hosts with at least 20 pages by error percentage, error count and coverage.

**Pattern:** Latest row per entity → conditional aggregation → top-N.
Simplest form: `latest(events, key, time) → GROUP BY category → ORDER BY metric LIMIT N`.

**Run:** 2026-09-14 06:09:42 UTC; SDK 0.8.0 from local source;
`public-query-v16:experimental`; snapshot 426669 for execution and profile;
no compiler rewrites. Warm service following exploratory requests.

| Metric | Value |
| --- | --- |
| API / client time | 166.35 / 316.81 ms |
| Separate profile engine time | 156 ms |
| Rows / row JSON bytes / truncated | 20 / 1,475 / no |
| Files | visits 8; documents 159; one inlined table per scan |
| HTTP | 188 GETs; 123.2 KiB received |
| Peak memory / spill | Unavailable |

**SQL**

```sql
WITH latest AS (
    SELECT page_url, http_status_code, captured_at
    FROM experimental.capture
    QUALIFY row_number() OVER (
        PARTITION BY page_url
        ORDER BY captured_at DESC, capture_id DESC
    ) = 1
), host_health AS (
    SELECT
        lower(regexp_extract(page_url, '^https?://([^/:?#]+)', 1)) AS host,
        count(*) AS pages_checked,
        count(*) FILTER (WHERE http_status_code >= 400) AS error_pages,
        count(*) FILTER (WHERE http_status_code BETWEEN 400 AND 499) AS client_error_pages,
        count(*) FILTER (WHERE http_status_code >= 500) AS server_error_pages,
        count(*) FILTER (WHERE http_status_code IS NULL) AS unknown_status_pages,
        round(100.0 * count(*) FILTER (WHERE http_status_code >= 400)
              / nullif(count(http_status_code), 0), 2) AS error_pct,
        max(captured_at) AS most_recent_capture
    FROM latest
    GROUP BY host
    HAVING count(*) >= 20
)
SELECT * FROM host_health
ORDER BY error_pct DESC, error_pages DESC, pages_checked DESC, host
LIMIT 20;
```

**Actual plan:** Visits scan (191,296 rows) + HTML documents scan (185,190)
→ hash join (185,190) → row-number window → latest-URL filter (152,684)
→ host hash aggregation (1,870) → minimum-coverage filter (423) → top-N (20).

**Access path:** Broad projected-column scans of day-partitioned visits and
bucket-partitioned documents. No HTML text or term-index reads. The limit does
not bound upstream scanning.

**Result:** All 20 returned hosts had zero recorded errors. Leading coverage:
`finance.yahoo.com` 11,705 pages; `www.tori.fi` 9,785; `www.gov.br` 4,864,
including 52 unknown statuses. Unknown statuses are excluded from the error-rate denominator.

**Interpretation:** Expected broad analytics; no demonstrated schema/optimizer defect.
Retained HTML excludes acquisitions without content, so this is not an availability SLA.
The original error-only query completed with zero qualifying hosts: 184.11 ms API,
281.80 ms client, snapshot 426669, query ID
`0650a8dc-2782-41e8-9450-10bb688459a6`. Its SQL was the SQL above with
`AND count(*) FILTER (WHERE http_status_code >= 400) > 0` appended to `HAVING`,
and without `pages_checked DESC` in the final ordering. This was a successful empty
answer. Removing the error-only predicate was an unrequested change of selection;
the 20-row measurements above are preserved as a subsequent variant, not a replacement
for that original outcome.

The original profiling attempt used `EXPLAIN (ANALYZE, FORMAT JSON)` and was rejected
with `Invalid expression / Unexpected token` at `WITH`. The later variant used accepted
`EXPLAIN ANALYZE` syntax. This syntax correction did not justify changing the business SQL.

**Evidence:** Execution query ID `a96549de-cf93-45f6-9cdb-5e4ab3944d96`;
profile `936480d7-99c0-4b69-a8c0-914f4e5df3e7`.
Local raw artifacts: `.artifacts/query-benchmarks/business-health-20260914/`
(ignored, not required to read this report).

## 02 — Ransomware recovery research shortlist

**Use case:** Give a security analyst recently captured source URLs mentioning both
“ransomware” and “recovery”. Keep one latest matching successful HTML capture per
URL, then return the 20 most recently captured.

**Pattern:** Multi-word posting lookup → require both words → content-to-capture
join → latest matching capture per URL → top-N.

**Outcome: FAILED — server query time limit exceeded on valid catalogue SQL.**
The platform did not deliver an answer under the settings in effect. No answer was returned for this business case.

### Attempt 1 — Original SQL

**Run:** 2026-09-14, production experimental API through local SDK 0.8.0;
client timeout 180 seconds. Exact server duration limit and failed-request compiler
metadata were not captured. The error came from the server, not the client deadline.

| Metric | Value |
| --- | --- |
| Outcome | Server query timeout |
| Error | `Query time limit exceeded. Reduce the work before retrying.` |
| API / client duration | Unavailable; failure timing was not retained |
| Rows / snapshot / query ID | Unavailable; no successful result returned |
| Actual plan / files / HTTP / memory / spill | Unavailable |

**SQL**

```sql
SELECT c.page_url, c.captured_at, s.score AS matched_terms
FROM experimental.search(['ransomware', 'recovery']) AS s
JOIN experimental.capture AS c ON c.content_id = s.content_id
WHERE s.score = 2
  AND c.http_status_code BETWEEN 200 AND 299
QUALIFY row_number() OVER (
    PARTITION BY c.page_url
    ORDER BY c.captured_at DESC, c.capture_id DESC
) = 1
ORDER BY c.captured_at DESC, c.page_url
LIMIT 20;
```

**Access path:** Intended multi-term index search plus capture enrichment. No actual
profile was obtained, so physical cardinalities, pruning and the timeout cause are unknown.

**Interpretation:** Platform failure for this business case. Performance classification
is provisionally unclassified: there is insufficient evidence to distinguish catalogue
shape, optimizer behavior or physical execution conditions. No valid runtime or
empty-result conclusion can be inferred from a timeout.

**Evidence:** Exact SQL retained at
`.artifacts/query-benchmarks/business-ransomware-20260914/timed-out.sql`;
the SDK tool output recorded the server error. No production rerun was made to correct
this report.

## 03 — Agency page audit trail

**Use case:** Review the capture history of the FBI crime-reporting landing page.

**Pattern:** SELECT WHERE url = literal ORDER BY time.

**Hypothesized access path:** Exact URL predicate on visits, join to HTML documents, then a small ordered history.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:25.215176+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 130.33 ms / 192.66 ms |
| Preparation client time | 76.86 ms |
| Profile engine / API / client time | 0.127 s / 133.13 ms / 194.13 ms |
| Rows / JSON row bytes / truncated | 1 / 154 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 188 / 123.2 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT capture_id, captured_at, http_status_code, content_id, byte_length
FROM experimental.capture
WHERE page_url = 'http://www.ic3.gov/default.aspx'
ORDER BY captured_at DESC, capture_id DESC
LIMIT 100;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `PROJECTION`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** documents: 1 emitted rows, 157 files; visits: 1 emitted rows, 12 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/03-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `capture_id`, `captured_at`, `http_status_code`, `content_id`, `byte_length`.

```json
["075dc086-adb8-499c-8796-589ee28136e5", "2026-09-13T03:05:52.208062+00:00", 200, "b592605f373bd94986771344864d35af5eb269c4aae6478bcbb76a40025d29be", 52907]
```

**Observed access behavior:** The exact URL returned one capture, but both evidence relations read many files (157 documents, 12 visits); selective output did not imply narrow file access.

**Interpretation:** Retained HTML history only; capture dates are not publication dates. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `85bd7260-40df-484f-8012-93ecc91efc62`; execution `5479a6ad-e9e8-4687-915c-cab22df293f5`; profile `893705b3-7d29-461b-999d-4c4e0873f44a`. Frozen SQL SHA-256 `378ba8314f2a9de7745bb2d4f506448fc50eda4c991e3e99c9eb9839939f6c0f`.

## 04 — Daily publishing-monitor coverage

**Use case:** Measure newly captured pages and bytes per hour for September 13.

**Pattern:** Time range → date bucket → aggregate.

**Hypothesized access path:** Timestamp range may prune visit day partitions; document join supplies content bytes.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:25.683204+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 113.68 ms / 171.57 ms |
| Preparation client time | 63.56 ms |
| Profile engine / API / client time | 0.105 s / 111.56 ms / 266.37 ms |
| Rows / JSON row bytes / truncated | 5 / 242 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 176 / 107.8 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT date_trunc('hour', captured_at) AS hour,
       count(*) AS captures, count(DISTINCT page_url) AS urls,
       sum(byte_length) AS html_bytes
FROM experimental.capture
WHERE captured_at >= TIMESTAMPTZ '2026-09-13 00:00:00+00'
  AND captured_at < TIMESTAMPTZ '2026-09-14 00:00:00+00'
GROUP BY hour ORDER BY hour;
```

**Actual plan:** Operators in plan display order (not execution sequence): `ORDER_BY`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** visits: 4,477 emitted rows, 1 files; documents: unavailable emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/04-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `hour`, `captures`, `urls`, `html_bytes`.

```json
["2026-09-13T00:00:00+00:00", 1224, 1224, 968687455]
["2026-09-13T01:00:00+00:00", 1722, 1722, 1411511948]
["2026-09-13T03:00:00+00:00", 631, 631, 259696545]
```

**Observed access behavior:** The time range read one visit file, consistent with day pruning, while the document side still read 159 files.

**Interpretation:** Measures capture throughput and repeated logical bytes, not origin publishing activity or stored compressed bytes. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `935ad720-add1-4d85-afdd-6d6d0d8aa6d6`; execution `a5a5ea60-d5b3-4910-a7c7-6177ce58cfe8`; profile `3fe3f41a-7c16-4342-90b6-1588e9fd7072`. Frozen SQL SHA-256 `f4474e4410600ac42ff75292f76873a93cdce47184c179ca563ba8a4f689cb98`.

## 05 — Government redirect destination review

**Use case:** Find government URLs whose retained capture ended at another hostname.

**Pattern:** Expression filter → unequal derived keys → top-N.

**Hypothesized access path:** Host extraction on both URL columns likely scans projected visit strings rather than a host index.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:26.189676+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 175.81 ms / 236.74 ms |
| Preparation client time | 64.91 ms |
| Profile engine / API / client time | 0.149 s / 156.11 ms / 216.23 ms |
| Rows / JSON row bytes / truncated | 30 / 5624 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 184 / 118.8 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT page_url, effective_url, captured_at
FROM experimental.capture
WHERE regexp_matches(page_url, '^https?://[^/]+\.gov(/|$)')
  AND lower(regexp_extract(page_url, '^https?://([^/:?#]+)', 1)) <>
      lower(regexp_extract(effective_url, '^https?://([^/:?#]+)', 1))
ORDER BY captured_at DESC, page_url, capture_id
LIMIT 30;
```

**Actual plan:** Operators in plan display order (not execution sequence): `PROJECTION`, `TOP_N`, `FILTER`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** documents: 185,190 emitted rows, 159 files; visits: 175,532 emitted rows, 6 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/05-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `page_url`, `effective_url`, `captured_at`.

```json
["https://www.uspsoig.gov/hotline", "https://hotlineform.uspsoig.gov/en-US/", "2026-09-13T03:05:56.433068+00:00"]
["https://secure.ssa.gov/pfrf/home", "https://oig.ssa.gov/fraud-reporting/splash/?URL=%2Fpfrf%2Fhome&LVL=3", "2026-09-10T05:34:52.942752+00:00"]
["https://usa.gov/", "https://www.usa.gov/", "2026-09-10T05:34:52.760866+00:00"]
```

**Observed access behavior:** Host-expression filtering used visit strings and a FILTER operator; it did not use a materialized host index.

**Interpretation:** Cross-host navigation is evidence for review, not proof of an unsafe redirect. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `581a854c-2c9a-4704-96cf-e563845ba4ce`; execution `6e5b82c7-84bf-437d-b489-4c9bac910231`; profile `d55100ae-e5d9-4247-b382-59291ace7891`. Frozen SQL SHA-256 `18a82baf082a71dcff5a31f97c3755ee3543bde8e262c753200c903f8fe933c6`.

## 06 — Duplicate-content consolidation

**Use case:** Find identical HTML served at multiple requested URLs to prioritize canonicalization review.

**Pattern:** GROUP BY content HAVING count(distinct URL)>1.

**Hypothesized access path:** Broad skinny content/URL join and distinct aggregation; content identity grouping avoids parsing HTML.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:26.711805+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 190.33 ms / 321.58 ms |
| Preparation client time | 61.83 ms |
| Profile engine / API / client time | 0.169 s / 179.88 ms / 247.66 ms |
| Rows / JSON row bytes / truncated | 20 / 3894 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 188 / 123.2 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT content_id, count(DISTINCT page_url) AS url_count,
       min(page_url) AS example_url, max(captured_at) AS last_seen
FROM experimental.capture
GROUP BY content_id
HAVING count(DISTINCT page_url) >= 5
ORDER BY url_count DESC, content_id
LIMIT 20;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `FILTER`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/06-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `content_id`, `url_count`, `example_url`, `last_seen`.

```json
["e8845dd235ccba766afddb67a1c5641d2fcb5bd8e6816c3e7ed9129af53bbebd", 335, "https://ir.apollo.com/_assets/_02cc8029b19b051d903c9c36e176b216/apollo/db/2220/22875/file/CORRECTED+TRANSCRIPT_+Apollo+Global+Management%2C+Inc.%28APO-US%29%2C+Bernstein+Strategic+Decisions+Conference%2C+28-May-2026+1_30+PM+ET.pdf", "2026-09-12T20:13:59.728618+00:00"]
["19f7a72741d9308bb6c5040629a6f96f09c59f055de64401324a04402dc30757", 218, "https://oakhill.com/2004/12/31/oak-hill-capital-partners-closes-investment-in-global-business-process-outsourcing-company/terms-and-conditions/", "2026-09-11T02:37:00.611958+00:00"]
["4ca286de7d0dd813e2dd180070716425249dd82fb144beb9e32894b4037dcb6a", 217, "https://oakhill.com/2004/12/31/oak-hill-capital-partners-closes-investment-in-global-business-process-outsourcing-company/privacy-policy/", "2026-09-11T02:37:06.052789+00:00"]
```

**Observed access behavior:** Hash grouping and distinct URL counts operated on broad visits/documents evidence scans.

**Interpretation:** Exact byte identity, not semantic duplicate detection; shared error or login pages may qualify. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `689570a7-90e6-42e0-bb5a-f72e2415951c`; execution `a0356051-5a7f-40ff-89a4-7a3cbfc1759f`; profile `73822b0c-c9f1-412c-a84e-0ca4046fdf98`. Frozen SQL SHA-256 `9faa74f30909d427049306203e7fbda350b3a5902de855770c6c0d12295075f0`.

## 07 — Investor-site content changes

**Use case:** Identify consecutive byte changes in captured BlackRock pages.

**Pattern:** Filtered history → LAG partition → inequality.

**Hypothesized access path:** URL-prefix scan followed by per-page ordered window; selected capture history still needs document identities.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:27.347433+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 137.06 ms / 207.72 ms |
| Preparation client time | 63.69 ms |
| Profile engine / API / client time | 0.127 s / 134.52 ms / 207.63 ms |
| Rows / JSON row bytes / truncated | 9 / 2077 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 188 / 123.2 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
WITH history AS (
 SELECT page_url, capture_id, captured_at, content_id,
        lag(content_id) OVER (PARTITION BY page_url ORDER BY captured_at, capture_id) AS previous_content
 FROM experimental.capture
 WHERE page_url LIKE 'https://www.blackrock.com/%'
)
SELECT page_url, captured_at, previous_content, content_id
FROM history
WHERE previous_content IS NOT NULL AND previous_content <> content_id
ORDER BY captured_at DESC, page_url, capture_id
LIMIT 30;
```

**Actual plan:** Operators in plan display order (not execution sequence): `PROJECTION`, `TOP_N`, `FILTER`, `WINDOW`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** visits: 1,588 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/07-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `page_url`, `captured_at`, `previous_content`, `content_id`.

```json
["https://www.blackrock.com/corporate/compliance/business-continuity", "2026-09-11T00:07:26.645720+00:00", "30c523e8cda52023bb8f1bb081257cb448e79bb6476e3a90e6d3e4cd8bdfe09d", "598f7ccde064839d2f3a646b067455a207e11f47cf0fa88d192f42089b778b55"]
["https://www.blackrock.com/corporate/compliance/privacy-policy", "2026-09-11T00:07:22.499250+00:00", "914dbd5fb49ff552da77d88a6e38c2c46113e11db74fd052fea278ea0dbebe7e", "7823645973b3b16b9db53302734c748f4c15eb96557bd43353cda2dae2e46eb5"]
["https://www.blackrock.com/us/individual/resources/regulatory-documents", "2026-09-11T00:07:21.713194+00:00", "c77637bc6fdf397261b5fae85f6037ee3ea19999eea4f0e23cde7ea6f72433dd", "da6aff011420483366a95b3e745ccf93eabd2ba90af9d46ce2dd7f558393f46d"]
```

**Observed access behavior:** The visit prefix reduced emitted rows to 1,588, but the document scan emitted 185,190 rows before the history window.

**Interpretation:** Any byte change qualifies, including templates and timestamps; no semantic change classification. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `772e7d27-4b62-475f-8627-f1fa4f7c0155`; execution `3aed9f72-3df7-4787-b656-66766fedf74b`; profile `5a6df73a-7e88-41d5-9367-789c822e8ae3`. Frozen SQL SHA-256 `b40619a616adfa29eb099516094a93196352c947bbb7beb49f714a93b9b6433e`.

## 08 — Page-weight budget outliers

**Use case:** Compare median and 95th-percentile retained HTML size for sites with at least 100 captures.

**Pattern:** Derived host GROUP BY → ordered-set quantiles.

**Hypothesized access path:** Broad byte-length projection and per-host quantile aggregation; no DOM access.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:27.833072+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 142.93 ms / 216.68 ms |
| Preparation client time | 64.79 ms |
| Profile engine / API / client time | 0.135 s / 141.23 ms / 201.05 ms |
| Rows / JSON row bytes / truncated | 20 / 1001 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 188 / 123.2 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT lower(regexp_extract(page_url, '^https?://([^/:?#]+)', 1)) AS host,
       count(*) AS captures, quantile_cont(byte_length, 0.5) AS median_bytes,
       quantile_cont(byte_length, 0.95) AS p95_bytes
FROM experimental.capture
GROUP BY host HAVING count(*) >= 100
ORDER BY p95_bytes DESC, host LIMIT 20;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `FILTER`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/08-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `host`, `captures`, `median_bytes`, `p95_bytes`.

```json
["www.yahoo.com", 1323, 7439698.0, 11135594.7]
["creators.yahoo.com", 108, 9291052.5, 9378369.35]
["www.newmountainfinance.com", 505, 22839.0, 7690428.999999996]
```

**Observed access behavior:** Quantiles used hash aggregation after broad evidence scans; output ranking did not avoid the upstream work.

**Interpretation:** HTML bytes exclude images, scripts fetched separately and network compression; captures are not deduplicated. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `f63786d1-4637-4a6a-b41d-39f1724e0c19`; execution `ea104164-1384-478b-bd24-e279887fb3a2`; profile `a44f5a54-7873-4f80-897f-bb8b82964bc7`. Frozen SQL SHA-256 `5cb591cdaed2d821b0688506df004d61a7c45f4e401e5e425c1d6f87ff494e84`.

## 09 — Referral sources to a reporting portal

**Use case:** List captured source pages linking directly to the IC3 homepage.

**Pattern:** Exact target lookup → join source identity → DISTINCT.

**Hypothesized access path:** Destination-equality access to link evidence followed by a capture-ID join; target pruning depends on link layout.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:28.319326+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 554.22 ms / 758.09 ms |
| Preparation client time | 65.56 ms |
| Profile engine / API / client time | 0.264 s / 272.91 ms / 351.37 ms |
| Rows / JSON row bytes / truncated | 8 / 700 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 376 / 246.5 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT DISTINCT c.page_url AS source_url, l.target_url
FROM experimental.link l
JOIN experimental.capture c ON c.capture_id = l.capture_id
WHERE l.target_url = 'https://www.ic3.gov/'
ORDER BY source_url LIMIT 50;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `LEFT_DELIM_JOIN`, `TABLE_SCAN`, `COLUMN_DATA_SCAN`, `DELIM_SCAN`.

**Observed scans:** link_occurrences: 15 emitted rows, 6 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/09-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `source_url`, `target_url`.

```json
["http://www.ic3.gov/default.aspx", "https://www.ic3.gov/"]
["https://finance.yahoo.com/markets/stocks/articles/know-risks-pre-ipo-funds-092000918.html", "https://www.ic3.gov/"]
["https://finance.yahoo.com/real-estate/articles/fbi-issues-warning-us-homeowners-105500748.html", "https://www.ic3.gov/"]
```

**Observed access behavior:** The exact target scan emitted 15 link occurrences from six files, while public-view expansion introduced repeated evidence scans and a semi-join.

**Interpretation:** Observed hyperlinks are not clicks, traffic or endorsements; only the exact normalized destination qualifies. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `c5ade168-e889-40db-ad4f-80266f18400f`; execution `821c5d4f-7a1a-4262-b9cf-a27362325457`; profile `b9005565-594b-413e-a823-1fb3384dac9a`. Frozen SQL SHA-256 `d75fb3728f1e125d96feaa3ddce91c3ae69966443f0e123362ceefe15ce9c2ca`.

## 10 — Agency outbound link inventory

**Use case:** Inventory destinations linked from the most recent IC3 landing-page capture.

**Pattern:** Top-1 capture key → join links → grouped targets.

**Hypothesized access path:** Known visit scope should constrain link reads; tests dynamic key propagation into capture-owned navigation.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:29.500684+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 3195.92 ms / 3260.04 ms |
| Preparation client time | 66.67 ms |
| Profile engine / API / client time | 1.07 s / 1110.59 ms / 1222.77 ms |
| Rows / JSON row bytes / truncated | 50 / 2682 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 376 / 246.5 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
WITH selected AS (
 SELECT capture_id FROM experimental.capture
 WHERE page_url = 'http://www.ic3.gov/default.aspx'
 ORDER BY captured_at DESC, capture_id DESC LIMIT 1
)
SELECT l.target_url, count(*) AS occurrences
FROM experimental.link l JOIN selected s ON s.capture_id = l.capture_id
GROUP BY l.target_url ORDER BY occurrences DESC, l.target_url LIMIT 50;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `LEFT_DELIM_JOIN`, `TABLE_SCAN`, `COLUMN_DATA_SCAN`, `DELIM_SCAN`.

**Observed scans:** link_occurrences: 30,850,037 emitted rows, 8 files; visits: 1 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/10-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `target_url`, `occurrences`.

```json
["https://www.ic3.gov/", 4]
["https://www.ic3.gov/Home/About", 2]
["https://www.ic3.gov/Home/FAQ", 2]
```

**Observed access behavior:** Despite selecting one source capture, the link scan emitted 30,850,037 rows from eight files. The selected visit did not produce a narrow link scan in this profile.

**Interpretation:** Inventory is limited to retained and indexed navigation, not all possible interactive destinations. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `4a65ef5d-3230-482e-a325-35f600cab808`; execution `44c7411c-030d-4956-a80a-790e1ad344c4`; profile `fe51f28e-ed43-487c-8921-36244cd68b3f`. Frozen SQL SHA-256 `4f5d3b2ab99a08c879887469438058722ad66d616956cf99cbe069dbfe9dabc8`.

## 11 — Uncaptured government crawl opportunities

**Use case:** Find known .gov URLs that have no retained HTML capture.

**Pattern:** Distinct URL universe → NOT EXISTS anti-join.

**Hypothesized access path:** Public page union/deduplication plus anti-join to capture; output limit may not bound graph expansion.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:34.059560+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 2077.35 ms / 2182.59 ms |
| Preparation client time | 69.12 ms |
| Profile engine / API / client time | 1.91 s / 1935.53 ms / 2047.25 ms |
| Rows / JSON row bytes / truncated | 50 / 2151 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 564 / 369.8 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT p.url
FROM experimental.page p
WHERE regexp_matches(p.url, '^https?://[^/]+\.gov(/|$)')
  AND NOT EXISTS (SELECT 1 FROM experimental.capture c WHERE c.page_url = p.url)
ORDER BY p.url LIMIT 50;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `CTE`, `HASH_JOIN`, `TABLE_SCAN`, `HASH_GROUP_BY`, `PROJECTION`, `UNION`, `FILTER`, `DELIM_SCAN`, `CTE_SCAN`.

**Observed scans:** visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; link_occurrences: unavailable emitted rows, 8 files; visits: 191,121 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/11-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `url`.

```json
["http://4050bonds.resources.ca.gov/"]
["http://abortion.ca.gov/"]
["http://access.parks.ca.gov/parkinfo.asp?park=38&type=0"]
```

**Observed access behavior:** The plan expanded page through UNION/CTE and deduplication before an anti-join. The wide text plan abbreviates the link cardinality, so its exact count is unavailable.

**Interpretation:** No retained HTML does not mean never attempted; page includes effective and linked URLs. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `034381ce-13e6-4047-9d6f-2f580596b0b4`; execution `d933a3b6-86a7-40bf-b601-4995a1213b77`; profile `415c28bd-1325-4d38-b836-ae5e1ca40951`. Frozen SQL SHA-256 `8bd8796717824b5dbf01f5e9f7fdbb0526285bfe500c42c31629161448fc8af8`.

## 12 — Two-hop agency discovery

**Use case:** Discover destinations two hyperlink hops from the IC3 homepage through captured intermediary pages.

**Pattern:** Seed → edge join → URL-to-capture join → second edge → DISTINCT.

**Hypothesized access path:** Two-hop graph expansion with repeated link access and possible fan-out; no recursive traversal.

**Outcome:** SUCCESS — empty result.

**Run:** 2026-09-14T06:27:38.369153+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 3054.03 ms / 3125.56 ms |
| Preparation client time | 77.23 ms |
| Profile engine / API / client time | 3.62 s / 3776.14 ms / 3843.14 ms |
| Rows / JSON row bytes / truncated | 0 / 2 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 2046 / 174.7 MiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT DISTINCT l2.target_url
FROM experimental.capture seed
JOIN experimental.link l1 ON l1.capture_id = seed.capture_id
JOIN experimental.capture middle ON middle.page_url = l1.target_url
JOIN experimental.link l2 ON l2.capture_id = middle.capture_id
WHERE seed.page_url = 'http://www.ic3.gov/default.aspx'
ORDER BY l2.target_url LIMIT 50;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `CTE`, `HASH_JOIN`, `TABLE_SCAN`, `DELIM_SCAN`, `CTE_SCAN`.

**Observed scans:** visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; link_occurrences: unavailable emitted rows, 8 files; link_occurrences: unavailable emitted rows, 8 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; visits: 1 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/12-plan.txt) (local ignored artifact).

**Result sample:** Empty; no rows matched.

**Observed access behavior:** Two link scans and repeated evidence joins were performed even though the final two-hop result was empty. An empty answer did not imply zero work.

**Interpretation:** Combines retained captures across time; paths are not necessarily contemporaneous. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `136146cd-5163-4190-a4fb-2068c3c666ef`; execution `e3efda6f-6127-423c-9f06-1a4e2475d448`; profile `3601d25b-863a-424e-b3a5-b59996047d65`. Frozen SQL SHA-256 `cace5d12f7c1e182a42fa2d50903027be4168698bf16c7c202b0392d3f1bb6fa`.

## 13 — Frequently cited destination hosts

**Use case:** Rank hosts by the number of distinct captured pages linking to them.

**Pattern:** All edges → host expression → count distinct source.

**Hypothesized access path:** Broad navigation scan with high-cardinality distinct aggregation over destination hosts.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:45.425371+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 3888.44 ms / 4056.54 ms |
| Preparation client time | 63.67 ms |
| Profile engine / API / client time | 3.19 s / 3209.81 ms / 3273.37 ms |
| Rows / JSON row bytes / truncated | 20 / 531 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 480 / 24.0 MiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT lower(regexp_extract(l.target_url, '^https?://([^/:?#]+)', 1)) AS target_host,
       count(DISTINCT c.page_url) AS referring_pages
FROM experimental.link l
JOIN experimental.capture c ON c.capture_id = l.capture_id
GROUP BY target_host
ORDER BY referring_pages DESC, target_host LIMIT 20;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `HASH_JOIN`, `LEFT_DELIM_JOIN`, `TABLE_SCAN`, `COLUMN_DATA_SCAN`, `DELIM_SCAN`.

**Observed scans:** link_occurrences: 30,850,037 emitted rows, 8 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/13-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `target_host`, `referring_pages`.

```json
["www.linkedin.com", 94832]
["twitter.com", 50691]
["www.youtube.com", 49038]
```

**Observed access behavior:** The link scan emitted 30,850,037 rows before distinct referring-page aggregation, consistent with broad graph analytics.

**Interpretation:** Historical retained link coverage is not web-wide authority or a traffic metric. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `d660983c-5128-430b-8253-55cdd7c2a5cf`; execution `ef0b811f-d98e-4fe4-9640-8b29051948e7`; profile `31b41a3e-7fef-428d-90a6-bae9ca8d147b`. Frozen SQL SHA-256 `56fd08d47183e213b8a56db591fa867f5dac3baa34fbc63c441379e34363c113`.

## 14 — Missing-title SEO audit

**Use case:** Find captured GitHub documentation pages whose retained content has no nonempty title declaration.

**Pattern:** Filtered captures → correlated NOT EXISTS metadata.

**Hypothesized access path:** Anti-join against title projection; tests whether selected content IDs scope metadata reads.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:27:52.827227+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 57141.54 ms / 57322.73 ms |
| Preparation client time | 94.08 ms |
| Profile engine / API / client time | 4.76 s / 4775.55 ms / 4856.09 ms |
| Rows / JSON row bytes / truncated | 8 / 1405 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 508 / 8.1 MiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT DISTINCT c.page_url, c.content_id
FROM experimental.capture c
WHERE c.page_url LIKE 'https://docs.github.com/%'
  AND NOT EXISTS (
    SELECT 1 FROM experimental.html_metadata m
    WHERE m.content_id = c.content_id AND m.kind = 'title'
      AND trim(coalesce(m.value, '')) <> ''
  )
ORDER BY c.page_url, c.content_id LIMIT 30;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `RIGHT_DELIM_JOIN`, `HASH_JOIN`, `TABLE_SCAN`, `DUMMY_SCAN`, `UNION`, `DELIM_SCAN`.

**Observed scans:** visits: 3,133 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files; html_elements: 3,964 emitted rows, 92 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/14-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `page_url`, `content_id`.

```json
["https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-code-scanning", "df6a2bf75dbb1b8f418cef7054fe32e1f2dfb693aa995e9562949db2b3b30274"]
["https://docs.github.com/en/code-security/concepts/code-scanning/codeql/custom-queries", "df6a2bf75dbb1b8f418cef7054fe32e1f2dfb693aa995e9562949db2b3b30274"]
["https://docs.github.com/en/code-security/concepts/supply-chain-security/best-practices-for-maintaining-dependencies", "df6a2bf75dbb1b8f418cef7054fe32e1f2dfb693aa995e9562949db2b3b30274"]
```

**Observed access behavior:** The anti-join reached html_elements (92 files), not a separate title table. Its element scan emitted 3,964 rows; eight content versions survived the audit.

**Interpretation:** Historical content versions qualify independently; no claim about current live SEO status. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `75af0bb2-1228-4737-87d6-c255d04a0c95`; execution `67a85d49-67e4-405a-b97d-2d8bdade062a`; profile `0f94a7c5-6de2-49f4-bf2e-6be108096056`. Frozen SQL SHA-256 `0ae7d803b07094c9b9f0bf9dc0534c9d7071b743da82bd11192061b0f2f73b45`.

## 15 — Declared-language market coverage

**Use case:** Count content documents by their explicitly declared HTML language.

**Pattern:** Metadata kind/name equality → value GROUP BY.

**Hypothesized access path:** Predicate pushdown into language metadata branch with distinct content counts.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:28:55.105964+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / 120619.74 ms |
| Preparation client time | 62.01 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `408`; code `resource_limit`; Query time limit exceeded. Reduce the work before retrying. Failure phase: execution. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT lower(value) AS language, count(DISTINCT content_id) AS contents
FROM experimental.html_metadata
WHERE kind = 'html_attribute' AND name = 'lang'
GROUP BY language ORDER BY contents DESC, language LIMIT 30;
```

**Estimated plan:** Operators in plan display order (not execution sequence): `TOP_N`, `HASH_GROUP_BY`, `PROJECTION`, `UNION`, `DUCKLAKE_SCAN`.

[Full estimated plan](../.artifacts/query-benchmarks/business-campaign-20260914/15-plan.txt) (local ignored artifact).

**Interpretation:** Declared language may be missing, wrong or repeated; this is not detected prose language. The platform did not deliver an answer for this frozen query. Cause remains provisionally unclassified; absent profiling cannot distinguish schema, optimizer and physical execution conditions.

**Evidence:** prep `357f05b9-80e5-443b-be7f-045462e71a3d`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `f6d85b75d55b1973b5cc6e9b019306875ea56e4ed1b12d49cbc0dddc5b0ffb5d`.

## 16 — Open Graph image deployment audit

**Use case:** Find social-preview images declared with insecure HTTP URLs.

**Pattern:** Metadata property equality + value prefix.

**Hypothesized access path:** Metadata branch and name filters plus attribute-value prefix scan; URL attributes do not use word postings.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:30:55.793362+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 7718.18 ms / 7865.35 ms |
| Preparation client time | 96.16 ms |
| Profile engine / API / client time | 2.36 s / 2370.64 ms / 2445.08 ms |
| Rows / JSON row bytes / truncated | 30 / 5233 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 12 / 10.4 MiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT content_id, node_index, value AS image_url
FROM experimental.html_metadata
WHERE kind = 'meta_property' AND name = 'og:image' AND value LIKE 'http://%'
ORDER BY content_id, node_index LIMIT 30;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `UNION`, `PROJECTION`, `TABLE_SCAN`.

**Observed scans:** html_elements: 222 emitted rows, 92 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/16-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `content_id`, `node_index`, `image_url`.

```json
["000628549c74308752746f2c43d4ba5549c7128c966a1504fd32ed66b5c556cb", 16, "http://www.waterland.nl/sites/waterland/files/styles/open_online_2_1/public/2024-02/7a6e1573-b174-4167-9718-d924cce81ccc.jpg?h=cd5f902d&itok=mXRicPX6"]
["0031bc491cad49ca84032fe0b701509938fe4677b1939a1be6f10c6608c481e3", 36, "http://berkshirepartners.com/wp-content/uploads/2025/12/Yi-Lilly_WEB.jpg"]
["003b99c555c23c3a9cae12c508957e40360fb38238d69c99590cf3b25df2df21", 34, "http://berkshirepartners.com/wp-content/uploads/2022/07/MicrosoftTeams-image.png"]
```

**Observed access behavior:** Metadata expanded to html_elements; property/prefix filtering emitted 222 rows from 92 files before top-N.

**Interpretation:** HTTP declaration is an audit candidate; redirects and actual mixed-content behavior are untested. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `8c6ffcc0-78ee-4b38-b8ea-7c82b58f4062`; execution `9338bf96-7005-40f5-973f-a2d2a4fb19a5`; profile `c5aaa6e3-c490-4afa-bc80-4ddc569deb3e`. Frozen SQL SHA-256 `8cfbb60a97f810f38abb504749442c83bfb28d098c05147c5034d3288594b9ac`.

## 17 — Structured-data parse failures

**Use case:** Summarize malformed JSON-LD scripts for structured-data quality monitoring.

**Pattern:** IS NOT NULL filter → error GROUP BY.

**Hypothesized access path:** Scan precomputed JSON-LD parse status without parsing DOM or JSON again.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:31:06.207651+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 1013.30 ms / 1182.21 ms |
| Preparation client time | 67.97 ms |
| Profile engine / API / client time | 0.0119 s / 18.10 ms / 77.80 ms |
| Rows / JSON row bytes / truncated | 2 / 64 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 0 / 0 bytes |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT parse_error, count(*) AS scripts, count(DISTINCT content_id) AS contents
FROM experimental.html_jsonld
WHERE parse_error IS NOT NULL
GROUP BY parse_error ORDER BY scripts DESC, parse_error;
```

**Actual plan:** Operators in plan display order (not execution sequence): `ORDER_BY`, `HASH_GROUP_BY`, `PROJECTION`, `TABLE_SCAN`.

**Observed scans:** html_jsonld: 389 emitted rows, 21 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/17-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `parse_error`, `scripts`, `contents`.

```json
["Invalid JSON syntax", 375, 328]
["Empty JSON-LD script", 14, 14]
```

**Observed access behavior:** The precomputed JSON-LD parse-status scan emitted 389 failing scripts from 21 files; the result has two error categories.

**Interpretation:** Only retained projected scripts are covered; valid JSON can still violate schema.org semantics. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `720efcae-17e5-477a-b9ee-3b941bf7fd9f`; execution `aef0fdc5-a047-4b7c-a151-753b6bed9524`; profile `d655a3df-9f41-4f6f-bae9-ae233b662a28`. Frozen SQL SHA-256 `0ea58534f8f89a357da783f0e838fe17281512d31225c633618e87d003fb6db1`.

## 18 — Product structured-data discovery

**Use case:** Find pages declaring top-level Product JSON-LD entities for a product research feed.

**Pattern:** JSON scalar extraction predicate → content join.

**Hypothesized access path:** JSON value scan for @type equality and late URL enrichment; no relational Product index assumed.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:31:07.539396+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 1795.55 ms / 2109.79 ms |
| Preparation client time | 63.15 ms |
| Profile engine / API / client time | 1.51 s / 1514.30 ms / 1582.17 ms |
| Rows / JSON row bytes / truncated | 30 / 3682 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 188 / 123.2 KiB |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT c.page_url, j.node_index, json_extract_string(j.value, '$.name') AS product_name
FROM experimental.html_jsonld j
JOIN experimental.capture c ON c.content_id = j.content_id
WHERE json_extract_string(j.value, '$."@type"') = 'Product'
ORDER BY c.page_url, j.node_index, c.capture_id LIMIT 30;
```

**Actual plan:** Operators in plan display order (not execution sequence): `PROJECTION`, `TOP_N`, `HASH_JOIN`, `FILTER`, `TABLE_SCAN`.

**Observed scans:** html_jsonld: 154,035 emitted rows, 21 files; visits: 191,296 emitted rows, 8 files; documents: 185,190 emitted rows, 159 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/18-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `page_url`, `node_index`, `product_name`.

```json
["https://about.instagram.com/features/direct", 252, "Instagram Direct Messages"]
["https://about.instagram.com/features/direct", 255, "Instagram Direct Messages"]
["https://about.instagram.com/features/reels", 252, "Instagram Reels"]
```

**Observed access behavior:** The JSON-LD scan emitted 154,035 scripts; scalar Product filtering and URL enrichment happened above that scan.

**Interpretation:** Top-level scalar Product types only; arrays and @graph are intentionally outside this query. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `31fc5f5e-fa93-42fa-a7af-a415e979bc7b`; execution `ba085b57-75db-4017-95dd-fafd8c8c6008`; profile `477d89f9-c2b5-4f14-b062-c1d2937bb95d`. Frozen SQL SHA-256 `bcda9ec5466e7131931ed5711a00bbdbaeae7671d6b2470d80edcb76cb9724b8`.

## 19 — Structured offer price comparison

**Use case:** Rank top-level USD Product offers by their declared numeric price.

**Pattern:** JSON path extraction → TRY_CAST → numeric range → top-N.

**Hypothesized access path:** Nested JSON path evaluation and computed numeric ordering require scanning candidate JSON values.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:31:11.297753+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 331.99 ms / 393.41 ms |
| Preparation client time | 65.95 ms |
| Profile engine / API / client time | 0.302 s / 312.99 ms / 382.82 ms |
| Rows / JSON row bytes / truncated | 2 / 213 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 0 / 0 bytes |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT content_id, node_index, json_extract_string(value, '$.name') AS product_name,
       try_cast(json_extract_string(value, '$.offers.price') AS DOUBLE) AS price
FROM experimental.html_jsonld
WHERE json_extract_string(value, '$."@type"') = 'Product'
  AND json_extract_string(value, '$.offers.priceCurrency') = 'USD'
  AND try_cast(json_extract_string(value, '$.offers.price') AS DOUBLE) > 0
ORDER BY price DESC, content_id, node_index LIMIT 20;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `PROJECTION`, `FILTER`, `TABLE_SCAN`.

**Observed scans:** html_jsonld: 154,035 emitted rows, 21 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/19-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `content_id`, `node_index`, `product_name`, `price`.

```json
["853077ae9ffdba4757547facd7ea6a1fc6465133e1c039eb144992ad516420d4", 933, "Disney+ and Hulu bundle ", 11.99]
["95031dc32608ba9bdf6d5c6a41d4d520aea52b80dbd044195afa3d62792fc1ec", 942, "Disney+ and Hulu bundle ", 11.99]
```

**Observed access behavior:** The same 21-file JSON-LD scan supplied 154,035 scripts for computed-price filtering; only two offers qualified.

**Interpretation:** Declared historical prices are unverified; nested offer arrays and currency conversion are excluded. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `e4199222-0838-413e-a978-4f6498db5a49`; execution `b2270eef-6417-4492-81f3-e28c0f2104a2`; profile `7ef4c11e-8bcf-4b87-ac49-74e8ae8ff1e1`. Frozen SQL SHA-256 `039f0bd7aa189d05cedb64749cecc2b92185275e3560ef787c4b2c9a00e58128`.

## 20 — Known-document heading outline

**Use case:** Extract the heading outline of a previously observed Brazilian entrepreneur portal document.

**Pattern:** Literal content key + tag IN → node order.

**Hypothesized access path:** Exact content-bucket and sorted-content pruning of element storage; only heading text projected.

**Outcome:** SUCCESS — empty result.

**Run:** 2026-09-14T06:31:12.145591+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 436.73 ms / 788.70 ms |
| Preparation client time | 61.99 ms |
| Profile engine / API / client time | 0.0396 s / 46.10 ms / 196.59 ms |
| Rows / JSON row bytes / truncated | 0 / 2 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 0 / 0 bytes |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT node_index, tag, text
FROM experimental.html_element
WHERE content_id = 'cd485888452d35669f61af0a0ab2fca44b8642a30145232b74e43be48e51210e' AND tag IN ('h1', 'h2', 'h3')
ORDER BY node_index LIMIT 100;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `FILTER`, `TABLE_SCAN`.

**Observed scans:** html_elements: 102 emitted rows, 11 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/20-plan.txt) (local ignored artifact).

**Result sample:** Empty; no rows matched.

**Observed access behavior:** Literal content scope read 11 element files and emitted 102 elements before the heading filter returned zero rows.

**Interpretation:** Content ID comes from the earlier SDK capture sample; empty output is a valid observation. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `c71eb589-cc36-4a4c-b412-0dd281765cb5`; execution `6a258857-cb04-4ff2-b77b-8b2e8fba61e1`; profile `a42ad489-3f43-478d-8bde-e39038041e92`. Frozen SQL SHA-256 `007e35b6b470c18701087dcd7cd18584ab8a7cb3cc118f52d4b16cc1ade68106`.

## 21 — Known-document component context

**Use case:** Inspect parent labels around buttons in the same portal capture for a usability review.

**Pattern:** Literal key → element self-join on parent node.

**Hypothesized access path:** Two content-scoped element scans with an equality self-join on parent identity.

**Outcome:** SUCCESS.

**Run:** 2026-09-14T06:31:13.200797+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 120.36 ms / 185.56 ms |
| Preparation client time | 65.00 ms |
| Profile engine / API / client time | 0.0465 s / 52.82 ms / 115.34 ms |
| Rows / JSON row bytes / truncated | 5 / 129 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 0 / 0 bytes |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT button.node_index, button.text AS button_text,
       parent.tag AS parent_tag, parent.text_direct AS parent_direct_text
FROM experimental.html_element button
JOIN experimental.html_element parent
 ON parent.content_id = button.content_id AND parent.node_index = button.parent_index
WHERE button.content_id = 'cd485888452d35669f61af0a0ab2fca44b8642a30145232b74e43be48e51210e' AND button.tag = 'button'
ORDER BY button.node_index LIMIT 50;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `PROJECTION`, `HASH_JOIN`, `FILTER`, `TABLE_SCAN`.

**Observed scans:** html_elements: 5 emitted rows, 11 files; html_elements: 55 emitted rows, 11 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/21-plan.txt) (local ignored artifact).

**Result sample:** First up to three returned rows; column order: `node_index`, `button_text`, `parent_tag`, `parent_direct_text`.

```json
[60, "", "div", ""]
[117, " Entrar com gov.br", "div", ""]
[121, " Ir para gov.br", "div", ""]
```

**Observed access behavior:** Both sides of the parent equality join read 11 files; five buttons joined to scoped parent elements.

**Interpretation:** Parsed text and DOM relationships do not establish visible labels or actual interactivity. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `428827a7-1069-4b47-88d8-316fbfe1aa8e`; execution `29972ad6-baa3-4e54-be04-6d554e613853`; profile `c07efc08-edd2-4c55-8afb-a3cf30aee6ee`. Frozen SQL SHA-256 `80cc07b06115df3a286511b08195ef5c167bef71ed5f6ac934c6790e76ca9a9a`.

## 22 — Known-document form content extraction

**Use case:** Extract text-bearing descendants of forms in the observed portal document.

**Pattern:** Scoped element self-join on subtree interval.

**Hypothesized access path:** Content-key pruning plus inequality range join over depth-first node intervals.

**Outcome:** SUCCESS — empty result.

**Run:** 2026-09-14T06:31:13.574204+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | 59.92 ms / 125.47 ms |
| Preparation client time | 162.13 ms |
| Profile engine / API / client time | 0.0536 s / 64.95 ms / 125.00 ms |
| Rows / JSON row bytes / truncated | 0 / 2 / False |
| Execution / profile snapshot | 426669 / 426669 |
| Profile HTTP GETs / received | 0 / 0 bytes |
| Peak memory / spill | Unavailable |

**SQL**

```sql
SELECT form.node_index AS form_node, child.node_index, child.tag, child.text_direct
FROM experimental.html_element form
JOIN experimental.html_element child ON child.content_id = form.content_id
 AND child.node_index > form.node_index AND child.node_index < form.subtree_end_index
WHERE form.content_id = 'cd485888452d35669f61af0a0ab2fca44b8642a30145232b74e43be48e51210e' AND form.tag = 'form'
 AND trim(child.text_direct) <> ''
ORDER BY form.node_index, child.node_index LIMIT 100;
```

**Actual plan:** Operators in plan display order (not execution sequence): `TOP_N`, `PROJECTION`, `HASH_JOIN`, `TABLE_SCAN`.

**Observed scans:** html_elements: 21 emitted rows, 3 files; html_elements: 0 emitted rows, 11 files. Counts are scan output rows, not rows physically examined.

[Full analyzed plan](../.artifacts/query-benchmarks/business-campaign-20260914/22-plan.txt) (local ignored artifact).

**Result sample:** Empty; no rows matched.

**Observed access behavior:** The form-side scan emitted zero rows from 11 files, while the text side emitted 21 rows from three files; the range-join query returned no rows.

**Interpretation:** Nested forms can repeat descendants; direct text excludes descendant text by design. This run completed; it does not establish scaling behavior or a schema/compiler defect.

**Evidence:** prep `0e5e5df2-dcca-4948-bb90-2f2058f2ac3b`; execution `e75437a4-f64f-4a8c-8ecc-a28ca09ba1f7`; profile `582f19b2-2c76-4e06-a2b3-69e3e098efec`. Frozen SQL SHA-256 `2770a6a5b6a173edab24008a2c13e903bb9fbe9b0c8487373b96c791a40ec95c`.

## 23 — Exact consent-copy inventory

**Use case:** Locate elements carrying an exact standard cookie-consent sentence for a copy audit.

**Pattern:** Exact full-text equality → top-N.

**Hypothesized access path:** Experimental exact-text candidate lookup may use an interior word and row-ID verification; eligibility and activation must be observed.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:13.994836+00:00; experimental SDK API; compiler `public-query-v16:experimental`; applied rewrites: `[]`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / 12851.61 ms |
| Preparation client time | 70.65 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: execution. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT content_id, node_index, tag
FROM experimental.html_element
WHERE text = 'We use cookies to improve your experience.'
ORDER BY content_id, node_index LIMIT 30;
```

**Estimated plan:** Operators in plan display order (not execution sequence): `ORDER_BY`, `HASH_JOIN`, `DUCKLAKE_SCAN`, `TOP_N`.

[Full estimated plan](../.artifacts/query-benchmarks/business-campaign-20260914/23-plan.txt) (local ignored artifact).

**Interpretation:** Exact case, punctuation and whitespace matter; zero results are valid. No wording variants will be tried. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `3b6fedf7-0a1a-481d-9e75-cbe7f1d270b4`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `a51cd8ca95162576b84396677785f8a9cdb6c8b63bdd4ca19a0b155cecd8cdd3`.

## 24 — Acquisition-announcement phrase discovery

**Use case:** Find H1/H2 headings containing an acquisition-announcement phrase for deal research.

**Pattern:** Tag IN + leading-wildcard ILIKE.

**Hypothesized access path:** Corpus element text substring scan; exact word postings cannot substitute for substring semantics.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:26.922978+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 57.37 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT content_id, node_index, tag, text
FROM experimental.html_element
WHERE tag IN ('h1', 'h2') AND text ILIKE '%acquisition of%'
ORDER BY content_id, node_index LIMIT 30;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  Phrase presence is not confirmation of a completed transaction; content may be historical. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `b49dfc73b454f56df51af36c9a982c10321d000a0a6010c7e415d17738edbda4`.

## 25 — Quantum topic content discovery

**Use case:** Build a content-ID candidate queue for an analyst tracking the exact word quantum.

**Pattern:** One term → ordered content IDs.

**Hypothesized access path:** Single-term posting path with no capture enrichment or element-list projection.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:26.983894+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 46.13 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT content_id, score
FROM experimental.search(['quantum'])
ORDER BY content_id LIMIT 50;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  Word matches include all indexed parsed text; IDs are a review queue, not classified articles. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `37aaacd556e43a18da9b254af031b1d6812d734fa6cd9159afff90bf952f484d`.

## 26 — Climate-finance source coverage

**Use case:** Rank captured hosts by content mentioning at least two of climate, transition and finance.

**Pattern:** Multi-term threshold → enrichment → distinct aggregation.

**Hypothesized access path:** Multi-term posting aggregation feeds a capture join and host distinct counts; broader than single-term lookup.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:27.034361+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 46.20 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT lower(regexp_extract(c.page_url, '^https?://([^/:?#]+)', 1)) AS host,
       count(DISTINCT s.content_id) AS matching_contents
FROM experimental.search(['climate', 'transition', 'finance']) s
JOIN experimental.capture c ON c.content_id = s.content_id
WHERE s.score >= 2
GROUP BY host ORDER BY matching_contents DESC, host LIMIT 20;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  At least two independent words, not a phrase or a semantic climate-finance classification. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `d71007f36a189ad55e9b414cfac6f492931fae0e2d798438d1027936e2d930ac`.

## 27 — HTML component footprint

**Use case:** Count forms, tables and media elements to estimate corpus UI extraction opportunities.

**Pattern:** Tag IN → grouped count and distinct content.

**Hypothesized access path:** Broad skinny element tag/content scan with dictionary-friendly equality filters and distinct aggregation.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:27.085762+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 41.06 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT tag, count(*) AS elements, count(DISTINCT content_id) AS contents
FROM experimental.html_element
WHERE tag IN ('form', 'table', 'video', 'audio', 'iframe')
GROUP BY tag ORDER BY elements DESC, tag;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  Presence of an element does not prove active functionality or visible content. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `6da38dad6a9cd5ca1bc53b13289a50adbf65e513bcb46edba98f9ca5d36cf9ae`.

## 28 — Password fields on HTTP captures

**Use case:** Find HTTP-effective captures whose DOM contains password inputs for transport-security review.

**Pattern:** Attribute map equality → content join → URL prefix.

**Hypothesized access path:** Attribute-map extraction on input elements combined with URL-filtered capture evidence.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:27.130238+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 47.84 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT DISTINCT c.page_url, c.effective_url, e.content_id, e.node_index
FROM experimental.html_element e
JOIN experimental.capture c ON c.content_id = e.content_id
WHERE e.tag = 'input' AND lower(e.attributes['type']) = 'password'
  AND c.effective_url LIKE 'http://%'
ORDER BY c.page_url, e.content_id, e.node_index LIMIT 30;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  An HTTP page with a password field is a review candidate; form submission destination is not evaluated. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `cbadf7d894d1e62c093ece061b6514178040689a664c79a3e72d5568cb7e9fad`.

## 29 — Missing image alternative text

**Use case:** Count missing and empty alt attributes separately for an accessibility backlog.

**Pattern:** Tag filter → map membership/null checks → conditional aggregate.

**Hypothesized access path:** Element attribute-map scan with three conditional counters; no text postings.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:27.181027+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 48.23 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT count(*) AS images,
       count(*) FILTER (WHERE NOT map_contains(attributes, 'alt')) AS missing_alt,
       count(*) FILTER (WHERE map_contains(attributes, 'alt') AND attributes['alt'] = '') AS empty_alt,
       count(DISTINCT content_id) AS contents
FROM experimental.html_element
WHERE tag = 'img';
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  Empty alt can be correct for decorative images; raw counts are not accessibility violations. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `72660ea687e74bc54dbdad9a4196662a2abcb763b429049aa4220027f7f70374`.

## 30 — Linked document-format demand

**Use case:** Estimate known PDF, spreadsheet and Word URL demand before expanding acquisition support.

**Pattern:** Distinct public URL universe → regex classification → GROUP BY.

**Hypothesized access path:** Page view expansion and deduplication followed by URL suffix classification; scans graph-derived identities rather than file contents.

**Outcome:** FAILED.

**Run:** 2026-09-14T06:31:27.232137+00:00; experimental SDK API; compiler `unavailable`; applied rewrites: `unavailable`.

| Metric | Observation |
| --- | --- |
| API / client execution time | Unavailable / Unavailable |
| Preparation client time | 113.71 ms |
| Profile engine / API / client time | Unavailable / Unavailable / Unavailable |
| Rows / JSON row bytes / truncated | Unavailable / Unavailable / Unavailable |
| Execution / profile snapshot | Unavailable / Unavailable |
| Profile HTTP GETs / received | Unavailable / Unavailable |
| Peak memory / spill | Unavailable |

**Failure:** ApiError; HTTP `503`; code `None`; Periplus is temporarily unavailable. Failure phase: preparation. Client time is time to failure, not a completed query runtime. No SQL changes or retry.

**SQL**

```sql
SELECT lower(regexp_extract(url, '\.(pdf|xlsx?|docx?)([?#].*)?$', 1)) AS extension,
       count(*) AS urls
FROM experimental.page
WHERE regexp_matches(lower(url), '\.(pdf|xlsx?|docx?)([?#].*)?$')
GROUP BY extension ORDER BY urls DESC, extension;
```

**Unavailable plan:** No plan returned.

**Interpretation:** Preparation was rejected before analytical execution; this is not a measured SQL runtime.  URL suffix is a demand proxy, not verified MIME type or proof that a destination is accessible. The platform did not deliver an answer for this frozen query. Observed category: service availability failure. No access-path performance conclusion is possible; the underlying outage cause is not established.

**Evidence:** prep `unavailable`; execution `unavailable`; profile `unavailable`. Frozen SQL SHA-256 `fba81ee2d5e9c76fbb8ce7599367523763d91a12cb7b3dfa9f2c54926c105040`.
