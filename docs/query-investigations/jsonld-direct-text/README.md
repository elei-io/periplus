# JSON-LD direct-text access

Classification: schema/catalogue design. The view reconstructs immediate script
text through a node join and grouping even though html_element.text_direct already
stores the same ordered concatenation. Hypothesis: reading that field removes
the entire node scan and grouping, while preserving JSON values, declaration grain,
parse errors and type/namespace filtering. No new materialization is needed.

Seven new compiler-v4 production triggers joined selected captures to JSON-LD: six
successes took 22.8–35.9 seconds and one resource-limit rejection took 22.0 seconds.
The failed fingerprint is
`69baf8507f4d8c2c3e1489a171523ecafae57aab77d613c4c4c476da46920243`.
These contain CTEs/windows and would not have qualified for the removed content
scoping pass. Do not attribute their cost to its removal without paired evidence.

Validation begins with all JSON-LD declarations at one production snapshot, using
the shared bench and reverse order; exact complete values/types/multiplicity must
match before adoption. Then test selected-capture families and local edge cases.

## Production reader evidence

The unbounded all-declaration baseline was interrupted in normal execution at
snapshot 68739 after 120 seconds. It produced no complete results; no equivalence
or full-corpus speedup is claimed. The next case uses a fixed lexical content-ID
cohort (`'0' <= content_id < '1'`) so the baseline can finish. It is not a corpus
scaling experiment and ingestion may change cohort membership between pairs.

| Pair order | Snapshot | Complete rows each | Baseline ordinary / warm | Candidate ordinary / warm |
| --- | ---: | ---: | ---: | ---: |
| Baseline then candidate | 69076 | 1,679 | 29.345 / 5.048 s | 7.572 / 2.574 s |
| Candidate then baseline | 69243 | 1,683 | 17.712 / 3.894 s | 16.699 / 2.502 s |

Within each transaction all columns, types, values and multiplicities matched.
Candidate warm time was 49% and 36% lower respectively. Both baselines scanned
86 node files and emitted approximately 1.41 million text nodes; neither candidate
scanned the node relation. Both variants read the same element files within each
pair (96, then 97). All native optimizer passes remained enabled.

Environment: local direct reader; DuckDB 1.5.5, DuckLake d8a1881e; two threads,
488.2 MiB memory, 244.1 MiB spill. Catalogue digest:
`57b9cd76c581a7b42b18818388623f81ed82bd6ccdbadff498a5ba27a288250b`.
These are one warm profile per variant, not production p95s or proof of cold cache.
The profile peak-buffer counter varied with pair order (candidate higher in forward,
lower in reverse); no peak-memory improvement is claimed. No spill was reported.

Shared benchmark cases include all declarations, the bounded content cohort and
latest-capture extraction with a left join. `baseline.sql` preserves the original
catalogue expansion; `candidate.sql` is the proposed view body. `cohort.sql` and
`latest.sql` are paired candidate wrappers. The measured baseline used the installed
original view; after catalogue installation, use the saved original expansion for
an off-versus-on comparison rather than comparing the changed view to itself.

## Validation and rollout

Focused JSON-LD fixtures cover objects, arrays, graphs, duplicate declarations,
invalid/empty text, JSON null, scalar values, MIME whitespace/parameters, foreign
namespaces, source-only scripts and Unicode/entity text. A differential check
compares stored immediate text with ordered node reconstruction. A dependency
regression drops the node relation and verifies the view still returns its result.
The full `make check` passed: 667 backend tests (34 skipped), five SDK tests, shared
package checks/tests and both frontend checks/builds. All added benchmark cases bind.

Install the updated public catalogue through the normal setup flow; no material
projection changes, generation rebuild or public schema-version change is needed.
After deployment, rerun unchanged JSON-LD workloads through the public query service
and compare cohorts and resource failures. The campaign has not deployed this change.
Rollback is a revert and catalogue reinstall. Full-corpus baseline completion and
production-service latency remain unverified; broad element scans are still possible.

## Latest-capture family

This sanitized workload selects the latest capture per URL with a window and
left-joins JSON-LD declarations, retaining captures without matching declarations.
Both variants use the same fixed content cohort to keep the original view bounded.

| Pair order | Snapshot | Complete rows each | Baseline ordinary / warm | Candidate ordinary / warm |
| --- | ---: | ---: | ---: | ---: |
| Baseline then candidate | 69379 | 892 | 39.483 / 4.295 s | 4.685 / 2.477 s |
| Candidate then baseline | 69561 | 892 | 20.947 / 4.165 s | 22.847 / 2.390 s |

Complete rows/types/multiplicity matched within both transactions. Warm time fell
42–43%. The candidate eliminated 86/87 node files and approximately 1.44 million
node scan output rows. Element scans read the same 98/100 files within each pair,
but emitted 729 rows for the candidate versus 1,689/1,694 for the original. This
supports better predicate propagation after removing reconstruction. Ordinary
execution timings show order/network effects; one candidate-first ordinary run
was slower than the subsequent baseline, so no universal cold-latency claim.
