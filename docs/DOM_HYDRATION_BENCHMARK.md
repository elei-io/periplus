# Bounded DOM hydration benchmark

## Decision

Keep the flat element materialization. Bounded public selectors now use the native extension's
keyed table operator. It consumes runtime `content_id` values and executes the already-bound
`material.html_elements` DuckLake scan with an exact equality filter for one immutable document at
a time. DuckDB applies the user's joins, aggregation, ordering, and limit to the operator's complete
output.

This is an extension-native execution strategy, not another durable DOM representation or a
Python query runner. Any DuckDB client that loads the Atlas extension and catalogue receives the
same behavior from ordinary `dom.query_selector` and `dom.query_selector_all` SQL.

## User scenario

A user wants the main-content destinations from a bounded set of current pages:

```sql
WITH scope AS MATERIALIZED (
    SELECT DISTINCT visit.content_id
    FROM web.page AS page
    JOIN web.page_visit AS visit
      ON visit.page_visit_id = page.latest_page_visit_id
    WHERE visit.content_id IS NOT NULL
    LIMIT 1000
)
SELECT
    scope.content_id,
    match.element_index,
    dom.get_attribute(match.attributes, 'href') AS href
FROM scope
JOIN LATERAL dom.query_selector_all(
    scope.content_id,
    'main a[href]'
) AS match ON true
ORDER BY scope.content_id, match.element_index
LIMIT 5000;
```

Before the native keyed operator, the authored SQL bounded document work but DuckDB decorrelated
the lateral selector into a broad element scan and reduced it only at a later hash join.

## Compared execution strategies

- `authored_lateral`: the public query above.
- `native_keyed_selector`: the same selector using the extension-native exact-key scan.
- `single_in_stage`: the historical compiler idea—capture keys, hydrate all matching element rows
  with one literal `IN (...)`, then run the selector against a temporary table.
- `bounded_probe_stage`: hydrate a temporary element table through eight-document batches of
  literal equality scans, then run the selector.
- `bounded_selector_stage`: feed the same exact-probe batches directly into the existing native
  selector and retain only matched rows for the final relational query.

All temporary state was connection-local. The benchmark did not alter DuckLake. The executable
harness is [`backend/scripts/benchmark_bounded_dom_hydration.py`](../backend/scripts/benchmark_bounded_dom_hydration.py).

The staging strategies were research controls only. Production public SQL now uses
`native_keyed_selector` internally and requires no client-side staging.

## Results

The corpus contained 7,299,300 element rows. Times are warm `EXPLAIN ANALYZE` latency. Peak memory
is DuckDB's connection-level `system_peak_buffer_memory`. `Element scan output` is the cardinality
leaving DuckLake element scans before later relational filtering.

| Documents | Strategy | Warm time | Peak memory | Element scan output | Staged rows |
|---:|---|---:|---:|---:|---:|
| 100 | Authored lateral | 2.65 s | 834 MiB | 7,211,968 | — |
| 100 | Single `IN` stage | 2.71 s | 885 MiB | 7,299,300 | 102,751 |
| 100 | Bounded element stage | 3.48 s | 543 MiB | 102,751 | 102,751 |
| 100 | Bounded selector stage | 2.80 s | 473 MiB | 102,751 | 1,401 |
| 500 | Authored lateral | 5.68 s | 1,101 MiB | 7,298,062 | — |
| 500 | Single `IN` stage | 5.68 s | 1,347 MiB | 7,299,300 | 521,580 |
| 500 | Bounded element stage | 12.90 s | 1,324 MiB | 521,580 | 521,580 |
| 500 | Bounded selector stage | 9.50 s | 663 MiB | 521,580 | 8,774 |
| 1,000 | Authored lateral | 9.55 s | 1,549 MiB | 7,299,072 | — |
| 1,000 | Single `IN` stage | 9.37 s | 2,042 MiB | 7,299,300 | 1,008,144 |
| 1,000 | Bounded element stage | 23.83 s | 2,026 MiB | 1,008,144 | 1,008,144 |
| 1,000 | Bounded selector stage | 16.70 s | 666 MiB | 1,008,144 | 16,454 |

All result bags and ordered output rows were identical for all four strategies at all three scope
sizes. The final `TOP_N` reduced 16,454 complete matches to the authored 5,000-row result only after
all batches had completed.

The bounded selector path opened roughly two physical files per content identity and read more
compressed bytes than the global scan at 500 and 1,000 documents. That is the deliberate capacity
trade: more small exact probes and higher latency, but bounded live state and work proportional to
the selected contents rather than total element cardinality.

## Native extension result

The production implementation was retested after the lake grew to roughly 10.8 million element
rows. The comparison below used the same captured scopes and ordered result digest.

| Documents | Previous broad plan | Native keyed plan | Previous peak | Native peak | Result |
|---:|---:|---:|---:|---:|---:|
| 100 | 4.56 s | 5.14 s | 1,206 MiB | 495 MiB | 1,258 rows, exact |
| 500 | 7.24 s | 14.79 s | 1,500 MiB | 844 MiB | 5,000 rows, exact |
| 1,000 | 10.28 s | 25.60 s | 1,919 MiB | 969 MiB | 5,000 rows, exact |

The keyed plan deliberately exchanges latency for capacity: at 1,000 documents it used about half
the reported peak buffer memory while remaining well below one minute. A separate run of the live
public `dom.query_selector_all` macro with DuckDB's memory limit set to 512 MB completed in 26.53 s
and returned the same 5,000-row ordered digest. DuckDB's profiler reports connection-wide peak
buffer usage, including buffers outside the query's enforced allocation, so that metric can exceed
the configured limit; successful execution under the limit is the relevant capacity result.

The nested DuckLake scans are internal to the native table operator and are therefore opaque to
DuckDB's outer-plan scan-cardinality profiler. The implementation supplies an exact
`content_sha256 = content_id` constant filter to the bound DuckLake scan for each document; it does
not infer selected-row cardinality from the outer profiler.

## What the comparison rules out

### A captured `IN` list is insufficient

The historical Python compiler's staged shape remains logically exact, but its single `IN` scan
read every element row at every measured scope. At 1,000 documents it increased peak memory from
1.55 GiB to 2.04 GiB. Reintroducing that mechanism would add compiler and runtime machinery without
delivering physical capacity.

### Staging complete selected DOMs retains too much state

Literal equality probes reduced scan output exactly as intended. Retaining all selected element
rows until the final selector nevertheless consumed 2.03 GiB at 1,000 documents. This is better
physical access followed by the wrong materialization boundary.

### A plan-only rewrite cannot perform the complete transformation

The content identities are runtime results. A plan-time rewrite cannot turn them into literal
equality probes. The native keyed table operator provides the needed execution boundary inside the
same DuckDB connection and transaction: it consumes each runtime key once, uses the table entry's
already-bound DuckLake scan and snapshot, and preserves input multiplicity.

## Correctness contract for implementation

The native operator must:

- consume runtime keys through DuckDB without rerunning the authored scope;
- use the same bound DuckLake table entry and transaction snapshot for every probe;
- preserve scope multiplicity, including duplicate keys;
- parse or validate the selector once and fail the complete operation on any batch error;
- retain complete matches from every batch before applying global aggregation, ordering, or limits;
- preserve empty and missing-document behavior and never emit a partial result;
- enforce the existing one-document element ceiling and bounded content-scope policy; and
- compare full result bags with the authored plan in deterministic tests.

## Implementation decision

The acceptance signal is met: the public API is unchanged, full ordered results are identical,
reported peak buffer usage is materially lower, and the 1,000-document public query completes
under one minute with a 512 MB memory limit. Keep the native keyed operator and do not add a second
DOM materialization or a Python execution dependency.
