# Index-backed exact element text

Status: paired production-reader acceptance passed; production API verification pending.
Date: 2026-09-13. User-authorized work, bounded to 2.5 hours.

Classification: compiler/optimizer access-path gap over the existing term/content
projection, with physical layout and extraction locality contributing. The public
exact-text semantics are appropriate. Neither a content-key IN filter nor an index
semijoin alone avoided expensive text extraction on this production layout. This is
not proof that the current physical layout will scale to a billion captures.

## Accepted candidate

Experimental execution recognizes literal exact text on one direct html_element
relation. An interior ASCII word surrounded by spaces is a safe complete ICU token;
edge words are unsafe because cat<span>fish</span> has element text fish but only
the page token catfish. The original text equality is always retained.

Within the existing request transaction and deadline:

1. Bind and validate the original public SQL and canonical installed views.
2. Look up at most 129 term/content rows. Decline above 128 contents, 1,024 nodes
   per content, or 16,384 candidate nodes; never truncate matches.
3. Resolve candidate element identities with the stored exact code-point length
   through a skinny physical scan, yielding bounded native DuckLake row IDs.
4. Extract only the canonical public columns using row-ID membership plus an
   explicit minimum/maximum row-ID range, then apply the unchanged original SQL.

No row IDs are cached, persisted or exposed as public identities. All stages share
one snapshot, and candidate selection is included in every measured execution.
Public namespace validation precedes the compiler-generated private scan. Common
words, unsafe boundaries, parameters, collations, CTEs/joins, sampling, time travel
and exact selected-content queries are not rewritten. Dispersed row IDs may still
have expensive extraction; this remains experimental, with no universal speed claim.

## Evidence

Shared benchmarks/query runner; isolated production reader with the query service's
4 GiB managed memory, 90 GB spill and two threads, inside a 6 GiB diagnostic pod.
Each pair shares one frozen snapshot and requires complete identical columns, types,
values, multiplicity and ordering. First connection is not a cold-cache claim.
Ordinary warm executions include lookup and compilation; profile bytes/files are
unavailable and are not fabricated.

| Case and order | Baseline first → candidate first | Baseline warm → candidate warm | Equal rows |
| --- | ---: | ---: | ---: |
| content_id < '40', baseline first | 12.265 → 6.529 s | 12.352 → 3.926 s | 4 |
| content_id < '40', candidate first | 11.991 → 6.286 s | 12.675 → 4.045 s | 4 |
| Full corpus, candidate first | 101.239 → 21.402 s | not measured | 22 |
| Full corpus, baseline first | 83.261 → 34.863 s | not measured | 22 |

The first full-corpus candidate completed but exceeded the case's initial 20-second
regression target; it did not hit the 120-second execution deadline. The recorded
initial result remains a target failure. Subsequent regression target is 30 seconds,
with the same 120-second hard measurement deadline and unchanged resource settings.
The full-corpus baseline-first run also completed equivalently, but its 34.863-second
candidate exceeded the subsequent 30-second target. Both full runs show acceleration,
not a 30-second latency guarantee; neither target failure is erased or called a timeout.
The quarter-scope cases satisfy their 20-second target in both orders. All four final
pairs used snapshot 421400. Production verification must use equal duration budgets
and record any default-limit failures honestly.

Unsuccessful experiments are material evidence: a one-statement semijoin and early
full baselines exceeded 120 seconds. Content-only literal filtering showed no reliable
warm improvement (about 12.05 → 12.33 seconds on the quarter scope). Adding a required
IN filter alone did not solve extraction. Resolving row IDs without a numeric range
was slower (about 12.88 → 18.87 seconds warm). The range changed the result; a narrow
read-only extraction probe returned four rows in 1.96 seconds initially and 0.137
seconds on repeat, versus approximately 11 seconds for row-ID IN alone. This does
not establish file-pruning counts. A small native reproduction still reports eight
files for both forms, so the physical-work attribution remains an upstream question.

## Release and remaining checks

Search helper and bounded finalization ship separately in PR76. This compiler pass
requires no materialization changes, new schema, stored index or maintenance work.
Local differential fixtures cover ICU boundaries, Unicode lengths, quoting, empty
candidates, multiplicity, ranges, overflow, changed views and collations. Query-service
checks cover original public SQL, stable/experimental equality, prep without index
reads, parameters and streaming metadata. Full make check passed (backend, SDK, shared packages and both frontends). Production
API proof is required before closing the investigation. Stable is not promoted by this work.
