# Vocabulary-first body discovery

Classification: both. The current search design scans all prose; vocabulary and
postings offer an alternative access path. Runtime join/key filtering may prevent
that path from pruning reads, so measure propagation and physical I/O separately.

Hypothesis: scanning the smaller vocabulary for an ASCII substring, looking up its
term IDs in content postings, and verifying only candidate prose should reduce
normalization and text-scan work while returning the same complete content set.
Tests cover exact term IDs and vocabulary substring expansion separately. Exact
term lookup is not equivalent to substring search (`robot` also matches `robotics`).
This experiment does not assert coverage for arbitrary Unicode or punctuation-only
substring queries; it is not a production rewrite or a public term surface.

Run read-only against one production snapshot with two threads / 4 GiB, using the
shared benchmark's bounded collector, per-case deadlines and profiles. Compare
complete ordered result digests, never only the first 100 hits. Measure vocabulary,
postings, verification, and the composed candidate query. Repeat baseline/candidate
in reverse order to expose cache bias. No compaction, rebuild or deployment.

## Production evidence

Snapshot 346888, DuckDB 1.5.5 / DuckLake d8a1881e, two threads / 4 GiB configured.
The corpus has 155,192 prose contents, 1,263,034 vocabulary terms in two files,
and 79,284,685 content postings in 13 files. Prose occupies 11 files.
Runs used a read-only materializer connection, not the public query process.
No measurement is claimed to be cold; cache effects and shared-connection peak
memory counters prevent interpreting profile byte/memory fields as isolated costs.

For `robot`, complete ordered content sets were compared in the same transaction:

| Stage | First measured | Warm | Result rows |
| --- | ---: | ---: | ---: |
| Full body scan | 31.56 s | 23.36 s | 3,228 |
| Vocabulary substring lookup | 0.287 s | 0.034 s | 132 term IDs |
| Exact `robot` postings only | 0.165 s | 0.055 s | 828 |
| Expanded term-ID postings lookup | 0.656 s | 0.254 s | 3,228 |
| Verification with collected content keys | 9.30 s | 8.99 s | 3,228 |
| Naively composed vocabulary/postings/prose SQL | 25.77 s | 25.73 s | 3,228 |
| Full body scan, reverse order | 23.52 s | 30.40 s | 3,228 |

The baseline, expanded candidates, verified result, composed result and reverse
baseline all have the same complete ordered content digest. Exact-term lookup alone
is insufficient for substring semantics; vocabulary expansion includes `robotics`.

The composed plan still puts `contains(lower(nfc_normalize(text)), 'robot')` on the
prose scan. Merely writing MATERIALIZED CTEs does not prevent this expensive work.
The posting scan emits 35.75 million rows toward its join in the composed plan,
compared with 9.74 million in the literal-ID expansion plan. Both still read all
13 posting files. Literal key collection improves execution but does not establish
native exact multi-key file pruning. No upstream fix or production compiler rewrite
was added.

## Bounded snippet/node pipeline

`indexed_matches` explicitly collects vocabulary IDs and posting candidates before
reconstructing selected nodes with the prior `search_matches` prototype. It verifies
the original body substring from reconstructed body text and keeps checking ordered
candidates until the requested number of real hits is found. The cap is applied to
verified hits, not blindly to unverified candidates. Existing prototype budgets
remain explicit; very broad queries may exceed the candidate collection bound.

For `robot`, it found candidates in 1.263 seconds and returned 100 contents with
161 snippet/node pairs in 10.64 seconds total, reading 523,178 nodes. Every returned
content, snippet, node-index array and score equals the prior scan-based prototype
on the same snapshot. That prior run took 32.88 seconds for discovery and 47.76
seconds total. The end-to-end runs share an immutable snapshot but were separate
connections; the speed ratio is observational, not a cold-cache comparison.

A second query, `microcontroller`, returned all 18 matches. Full-body reference
discovery took 24.64 seconds. Indexed discovery took 0.485 seconds and the complete
snippet/node pipeline took 3.81 seconds, reading 101,741 nodes. Its complete content
set matched the reference.

Decision: vocabulary/postings are a useful discovery starting point. Preserve the
staged boundary and verify before constructing public matches. Do not promote the
naively composed SQL or claim billion-capture scaling: vocabulary substring scans
grow with distinct tokens, postings file reads currently cover all 13 files, broad
queries produce many candidates, and append/compaction behavior still needs testing.
The registered public case returns the bounded content set; the supporting experiment
uses internal SQL overrides to compare complete reference/candidate sets.
