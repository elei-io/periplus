# Correlated content counts

Classification: compiler/optimizer, provisionally. Fingerprint
`505df82a2a1d953a2e684088f82a0b69fbffa9f01f214f35e0c1a9cd0a16080e`
counts nodes, images and JSON-LD for URL-filtered captures before a result limit;
it completed in 37.647 seconds on compiler v4 / deployment sha-547bd2b.

Isolate the node count for a small selected content-key relation. Hypothesis:
decorrelation groups unrelated nodes before the key restriction, while an explicit
redundant semijoin inside the count enables input filtering. Both SQL forms retain
COUNT's zero behavior and the same key rows. This is research-only; no compiler
change or reactivation of the removed scoping pass. Use the shared paired bench.

The one-key baseline was interrupted in normal execution at snapshot 78305
after 60 seconds. There is no complete result/equivalence evidence from that run.
A bounded counterpart supplies a fixed lexical content cohort to both key selection
and node inputs; its additional predicate is redundant for every correlated match.
This permits measuring input semijoin reduction without requiring a full-corpus
count to finish. The unbounded case remains recorded, not silently replaced.

The first candidate fails during execution with DuckDB NotImplementedException.
An independent two-key in-memory reproduction confirms: “Unsupported join type
for flattening correlated subquery.” This is native decorrelation, not S3 access.
The failing SQL remains as `unsupported-correlated-semi.sql`. The revised research
candidate places key-scoped grouped counts in a materialized CTE and left-joins
them to the unchanged key rows, using coalesce to retain zero counts. No production
compiler code is changed.

## Bounded comparison

The original bounded baseline completed at snapshot 78403 before the unsupported
candidate failed: 23.469 s ordinary / 848.7 ms warm, one node file and 8,369 node
scan output rows. The additional cohort predicate already enables selective access.

The revised grouped-count candidate matched complete values/types in both orders:

| Order | Snapshot | Baseline ordinary / warm | Candidate ordinary / warm |
| --- | ---: | ---: | ---: |
| Baseline then candidate | 78513 | 23.897 s / 921.5 ms | 857.0 / 707.0 ms |
| Candidate then baseline | 78559 | 789.1 / 913.4 ms | 24.097 s / 872.1 ms |

Each variant reads one node file and emits 8,369 node rows. The warm improvements
(23% and 4.5%) do not demonstrate reduced primitive work; the large ordinary cost
follows first-variant order. Six local comparisons also preserve zero counts,
duplicate capture keys and null input keys. These results alone do not justify a
new compiler pass. Next measure the revised candidate against the original form
without the cohort predicate, using a 120-second budget to seek complete evidence.

## Unbounded grouped-count candidate

With a 120-second budget, candidate-first pairing completed at snapshot 78637.
Complete results and types match. Baseline ordinary/warm: 22.518 s / 5.001 s;
candidate ordinary/warm: 99.680 s / 693.6 ms. The candidate reads one node file
and emits 8,369 rows; the baseline reads 624 node files and emits 70,626,325 rows.
First-variant/network/cache effects dominate ordinary latency; no cold speedup
claim. Unlike the bounded case, physical work drops sharply. Opposite-order and
broader-family evidence are still required before an automatic compiler pass.

An independent ordinary-DuckDB reproduction with 1,000 item keys and two requested
keys (one absent) returns equal counts. The baseline forms 1,000 count groups;
the scoped grouped form creates one. This supports a decorrelation/key-propagation
cause, independent of HTML semantics and object storage.

The opposite-order unbounded run was interrupted in baseline normal execution at
snapshot 78765 after 120 seconds; its candidate did not run. No two-order complete
unbounded result exists. Do not activate a compiler rule from the one successful
pair. The next evidence must address execution-path/cache variability and test
multiple dispersed keys, not only the minimum content hash. Minimum-hash prefixes
cluster requested keys and must not be treated as representative random lookups
or a controlled unrelated-corpus scaling test. All reports remain private campaign
artifacts. No runtime changes or new PR are proposed from this investigation yet.

## Dispersed20-key validation

Hash-ranked20-key selection avoids the minimum-hash clustering in the first test.
Both complete comparisons preserve results/types (20rows):

| Order | Snapshot | Baseline ordinary / warm | Candidate ordinary / warm | Node files baseline / candidate |
| --- | ---: | ---: | ---: | ---: |
| Candidate first | 88554 | 9.689 s / 5.034 s | 25.120 s / 4.371 s | 355 / 259 |
| Baseline first | 88635 | 30.585 s / 5.008 s | 4.609 s / 4.476 s | 322 / 250 |

The candidate improves warm time11–13%, but node scan output remains44.62–44.87
million rows versus baseline80.95–81.18million. This is not adequate selective
access for20 keys. Do not present the earlier one-key result as general evidence
of stable lookup cost. The native key-join access path remains a separate blocker.
A fixed-key literal-membership versus key-table comparison is the next experiment;
it separates key propagation from physical file layout without capture selection.
Different snapshots and ongoing compaction explain why file counts are compared
within each pair rather than treated as controlled corpus-growth evidence.

## Fixed-key access-path isolation

Resolve20 distinct keys once and hold that exact set fixed, then compare grouped
counts over a VALUES-key semijoin with literal IN membership in each snapshot.
Private key literals stay in ignored artifacts; neither keys nor rows are published.
Both orders return19 equal rows/types (one selected key has no nodes).

- Snapshot88758: semijoin ordinary13.150 s/warm3.750 s, IN3.727 s/4.075 s.
  Both read261 node files; scan output45.10million versus48.87million.
- Snapshot88823 reverse: semijoin3.912 s/3.883 s, IN13.532 s/3.789 s.
  There is no repeatable latency improvement from literal IN.
- Snapshot88899, exact-key UNION ALL candidate first: equal19rows. Semijoin
  ordinary10.297 s/warm3.877 s, one scan263files and45.46million output rows;
  20 exact equality branches23.291 s/17.663 s,689 summed file reads and109,595
  scan output rows. Summed file reads can include the same file repeatedly and
  must not be called689 distinct files. This candidate is4.56x slower warm and
  is rejected without another reverse-order production run.

These measurements distinguish filter propagation from efficient batched access.
Exact predicates reduce scan output, but issuing one scan per key is too costly.
The current application declarations bucket both primitives by content_sha256
into8 partitions; dispersed keys quickly cover many buckets. This is a layout
risk, not proof of the active production partition specification or a recommendation
to increase buckets blindly. More buckets alone do not remove corpus-size scaling.
Next inspect the active partition specification and file key-range overlap, then
compare a clustered bounded-file layout in an isolated corpus. Avoid introducing
a generic compiler rewrite before there is a proven batched access path.
