# HTML title reconstruction

Classification: schema/catalogue design, with possible optimizer amplification.
Production fingerprint `e5939a7a79af0293634f2f2d36fe929e59928e9da09b8d5bcbfa87d58831c8fb`
completed in 58.191 seconds on compiler v4 / deployment sha-7f47394. It ranks
captures per URL, left-joins metadata titles and prose, then limits output.

Hypothesis: HTML-namespace title parsing produces RCDATA text, so existing
text_direct equals its descendant text; removing node reconstruction should
preserve every metadata declaration while avoiding node reads and grouping.
Foreign titles remain excluded. Six HTML titles across seven adversarial parser
fixtures matched; production equality is required before adoption. No runtime
change is active. The first paired case uses a fixed content-ID cohort so the
original baseline can complete.

## Full metadata cohort results

Each pair uses one production read transaction, fixed lexical content prefix
`'0' <= content_id < '1'`, all native optimizers enabled, DuckDB 1.5.5 / DuckLake
d8a1881e, two threads, 488.2 MiB memory and 244.1 MiB spill. Access is the developer
machine reader, not the deployed query service. One warm profile per variant.

| Order | Snapshot | Complete rows each | Baseline ordinary / warm | Candidate ordinary / warm |
| --- | ---: | ---: | ---: | ---: |
| Baseline then candidate | 74896 | 63,676 | 79.011 / 26.548 s | 32.760 / 21.353 s |
| Candidate then baseline | 75024 | 63,771 | 51.784 / 24.819 s | 53.079 / 25.291 s |

Complete columns/types/values/multiplicity matched within both snapshots. The
candidate eliminated the node scan: 103 files / 939,558 output rows in forward,
71 files / 942,843 output rows in reverse. All seven element scan branches remained,
with identical per-branch file counts within each pair (112, then 83). The changing
file counts are observations under maintenance, not a controlled compaction study.

Warm all-metadata performance improved 19.6% in forward but was 1.9% slower in
reverse. Therefore no repeatable all-metadata latency gain is established. The
node-work reduction is repeatable; the other metadata branches still dominate
this workload. Peak-buffer comparisons also vary by order; no memory claim.
A title-only latest-capture family comparison is required before a performance PR.

## Semantic basis and tests

The pinned html5lib 1.1 parser's `startTagTitle` invokes `parseRCDataRawtext` in
RCDATA mode, and `parse_document` stores the immediate text nodes in order. HTML
title text therefore has no descendant element text to reconstruct. Foreign
namespace titles stay excluded. Parser fixtures cover markup-looking text, entities,
Unicode, empty/duplicate titles, body/template contexts, SVG titles and integration
points, and select parsing. Direct text equals ordered descendant reconstruction.
A separate regression retains both title elements and meta declarations named
`title`, and verifies metadata works without the node relation.

`make check` passed: 669 backend tests (34 skipped), five SDK tests, shared package
checks/tests and both frontend checks/builds. New benchmark cases bind. No runtime
or catalogue changes have been deployed by this investigation.

The initial latest-capture family baseline exceeded 120 seconds without completing.
`latest-unrestricted.sql` preserves that SQL. The bounded counterpart explicitly
adds the same content-cohort restriction to the metadata input on both sides; it
cannot remove a matching right row because the left capture keys already obey
that restriction. This isolates the direct-text change from the separate missing
key-propagation problem. The interrupted form is not an equivalence result.

## Bounded title-only family

| Order | Snapshot | Complete rows each | Baseline ordinary / warm | Candidate ordinary / warm |
| --- | ---: | ---: | ---: | ---: |
| Baseline then candidate | 76578 | 20 | 65.784 / 7.369 s | 9.832 / 4.356 s |
| Candidate then baseline | 77344 | 20 | 38.159 / 7.993 s | 35.852 / 4.038 s |

Complete columns/types/values/multiplicity matched within both transactions.
Warm title-family time improved 41% and 49%. Node scans disappeared (86/96 files;
952,886/957,335 output rows). Element scans retained identical files within each
pair (98/108), with candidate outputs 415/420 versus baseline 1,248/1,258. This
supports better filtering after eliminating the title reconstruction barrier.
It does not establish that the original unbounded service incident is fixed.

Rollout: install through the ordinary public-catalogue setup flow. No projection
rebuild or schema-version change is required. Verify unchanged query-service
workloads after deployment. Rollback is a revert and catalogue reinstall. No
production data or configuration was changed during this investigation.
