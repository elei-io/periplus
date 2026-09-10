# Bulk lateral subtree extraction

Classification: optimizer/execution shape. The public helper has the right content
and subtree identity; its correlated expansion did not contain the intermediate
work within the public memory/spill budget. No public grain or text-policy change
is needed. The reusable case is `single-capture-subtree`: select one capture, join
its links/elements, and invoke `subtree_text` laterally for every link. The private
incident additionally restricted selection by request membership; that identifier
and raw results are not committed.

On deployment 9b7a6be, stable returned resource_limit after 27.26 seconds.
Experimental initially returned 503; that is an availability outcome, not a second
confirmed SQL memory failure. Capture selection took 208 ms; the query without
subtree text returned all 1,106 links in 1.77 s. The original physical plan had
unrestricted HTML node inputs (91.85 million estimated rows) and an enormous
estimated correlated root intermediate. Those are estimates, not executed row
counts. The selected page contains only 6,793 nodes.

## Intervention and evidence

Materialize the content-filtered node input inside the helper before root lookup
and descendant text aggregation. Keep argument validation, root bounds, character
prefix accounting, node limits, empty results, ordering, and all output types.
The portable macro changes; this is not a query compiler rewrite or new stored
materialization. Setup installs it in both endpoints without a rebuild.

A reader-only probe using the installed helper body and production snapshot
100520 completed the original query with the revised helper in 3.45 s initially
and 2.99 s on its subsequent profiled execution, at 512 MB memory / 256 MB spill /
two threads. All 1,106 ordered result rows and column types match the original
helper evaluated over the complete selected page's nodes, elements, links and
capture in temporary local tables in the same read snapshot. The original
whole-corpus execution did not complete at the public limits; this is scoped-input
equivalence evidence, not a completed before/after full-corpus timing pair.

An explicit outer selected-page CTE is a faster hand-written alternative:
1.29 s initial / 1.24 s warm in the shared bench, and 2.53 s through experimental.
It requires inlining the helper in caller SQL. It was used to isolate and validate
the cause; the deployed intervention belongs in the shared helper so existing SQL
benefits unchanged. Its full result digest equals the revised helper's digest.

The revised helper's node scan emits 6,793 rows and reads six files in the profile.
Reported rows_scanned is 183,709,654, so this does not establish selective physical
row-group pruning or corpus-independent I/O. DuckDB can repeat the scoped input
for distinct lateral bindings. This fixes the observed intermediate-work failure;
it is not a constant-memory or billion-content access-path guarantee.

## Regression and deployment

Differential fixtures compare the old helper in `baseline.sql` with the new helper
for multiple contents/roots/limits, duplicate calls, empty subtrees, missing roots,
null content, raw script text, comments and Unicode. A physical-work assertion
bounds the materialized input by selected content times caller bindings despite
100,000 unrelated nodes. Existing tests exercise all subtree text ordering,
whitespace, truncation boundaries, invalid limits and oversized-root errors.

Run the shared bench with `--case single-capture-subtree` for a sanitized workload;
use its frozen-snapshot and reverse-order protocol for subsequent candidates.
After deployment replay the original private incident query through both public
endpoints and record complete result/truncation state. Reverting the helper SQL
and redeploying restores the previous execution shape without a data rebuild.
