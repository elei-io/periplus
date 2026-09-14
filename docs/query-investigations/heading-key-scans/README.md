# URL-selected heading scans

Status: included in the separate experimental catalogue release; production verification pending.

Incident: query reference 07180a4a-f4b4-45a2-bb62-06ca5ea041f2.
Case: `benchmarks/query/cases/books-heading-search/`.
Classification: optimizer/scan access path. Exact content equality completes quickly;
static IN introduces optional membership filtering and much slower text extraction.
The public schema already exposes the necessary content key and stored text.

Production-service observations at snapshot 419445: ten individual keys took
41–364 ms each; a ten-branch equality UNION ALL took 436 ms (two complete matches).
Two-key IN took 6.1 seconds. Ten-key IN completed after the diagnostic client's
35-second deadline; server logs report 65.7 seconds and two rows. Adding redundant
list_contains also exceeded the client deadline (server: 55.5 seconds, two rows).
These are observations, not controlled physical-I/O measurements. EXPLAIN JSON
profiles are rejected by the current public SQL boundary. No profile equality is
claimed. All diagnostic server operations finished.

The original URL predicate selects 586 captures / 585 distinct contents. Required
semantics preserve capture multiplicity and substring matching. The bench removes
the unordered LIMIT to compare complete result multisets; production acceptance
must also run the original LIMIT 10 statement unchanged.

Hypothesis: bounded discovery followed by equality branches can avoid the optional
multi-key scan filter. Test selection and both complete query variants inside one
read transaction using the shared benchmark measurement functions, then reverse
order. Do not ship generated branches unless the full selected set is bounded and
measured faster. Keep the original capture join to preserve duplicate captures.

Acceptance: equal complete rows/types/multiplicities at one snapshot; repeatable
improvement under unchanged budgets; tested empty/null/duplicate keys, unsupported
grammar, parameter handling, shared deadline and snapshot. Runtime changes must be
experimental only. No timeout increase, metadata mutation or production rollout.

## Full-scope equality branch result

The shared bench inside a separate read-only process in the query pod measured
585 equality branches at snapshot 419445. Selection took 572 ms; complete
extraction took 117.04 seconds for 40 rows. This fails the 20-second target and
rejects unconditional equality-branch generation for the original query.
Effective settings were two threads, 4.0 GiB memory and 83.8 GiB spill (deployment
environment overrides bench defaults). No warm profiles have been collected.
This experiment is not a production service change.

## Exact membership with staged heading text

A second candidate resolves the same keys in the read transaction, materializes
`html_heading WHERE list_contains(constant_keys, content_id)`, then runs the
original join and text predicate. Unlike the unsuccessful user CTE, membership
is a resolved constant list and text searching is outside the materialized input.
The capture relation and join are retained; 39 matching heading rows produce
40 output rows because capture multiplicity is preserved.

All completed full-result variants below used snapshot 419445 and matched the
40-row digest `7f4f6dd40dc8d1cadcca6f8145d6517535b6286a2996c34be381948fe6d2d456`,
column names and types. The reference is the semantically equivalent equality
branch query, **not** a completed execution of the unmodified full query: both
unmodified full-query runs exceeded 120 seconds. Consequently the prescribed
complete native/candidate pair in both orders is still unavailable. Local
adversarial tests compare against the original SQL directly.

| Measurement | Ordinary elapsed | Result |
| --- | ---: | --- |
| Direct staged-membership bench | 20.598 s + 0.568 s selection | 40 rows |
| First isolated QueryService attempt, own stricter 60 s deadline | interrupted | incomplete |
| Traced isolated QueryService, original LIMIT 10 | 16.578 s total | 10 rows |
| Final-source isolated QueryService, original LIMIT 10 | 9.767 s total | 10 rows |
| Same service, subsequent full query | 3.338 s total | 40 rows, matching digest |
| Final guards/quoted-alias handling, original LIMIT 10 | 23.008 s total | 10 rows, verified subset of full result |
| Same service, subsequent full query | 4.663 s total | 40 rows, matching digest |
| Direct staged-membership profile run | 10.124 s + 0.600 s selection | 40 rows |
| Subsequent EXPLAIN ANALYZE in that read transaction | 3.504 s | 40 rows |

The authoritative production policy was read, not changed: 120 s, 10,000 output
rows and 16 MiB result bytes. Final service probes use that duration; there was no
policy increase. The service preserves the original request text, advertises
`capture_heading_exact_scope_v1`, and explains the candidate. These processes
connect read-only alongside the installed service; the live HTTP endpoint has
not been upgraded. Tests are sequential; no production data or configuration is
modified. These are first/subsequent reader observations, not a controlled cold
cache experiment or a latency percentile claim. The initial failed run remains
an unresolved variability observation.

### Profile and remaining access-path limitation

The successful warm profile has an exact `list_contains` filter and no ILIKE
filter in the `html_elements` scan. It emits 135,388 rows before the heading-tag
filter, and reports 116 element files read. Substring matching is above the
materialized CTE. Reported `rows_scanned` is 416,704,872; it is an engine counter,
not a claim that precisely that many physical rows were decoded. Reported total
warm-profile bytes are only 356,907, heavily affected by prior reads/caches and
not a cold-I/O estimate. Peak buffer counter is 5,170,389,002 bytes; reported spill
is zero. The candidate still accesses many files and has **not** passed the
fixed-key/unrelated-corpus growth contract. This is an execution improvement,
not a billion-capture architecture proof.

## Implementation boundaries

`query/heading_scope.py` is called only by experimental execution after original
SQL validation/binding. It accepts one plain inner capture/heading content-ID join
with a literal requested/effective URL predicate and a literal heading-text
LIKE/ILIKE predicate. Discovery collects at most 1,025 distinct keys to detect the
1,024-key activation bound; key bytes are capped at 128 KiB. Empty selection uses
an empty heading relation. Larger/unsupported selections execute natively under
the remaining original deadline. Parameters, nested scopes, CTEs, outer joins,
aggregations, windows, table samples and renamed table-column lists are excluded.
Public views are queried intact, without expanding or replacing their semantics.
Selection/extraction use the same connection, snapshot and timer. Preparation
never performs discovery. The combined release uses compiler v14; this optimization remains experimental-only.

Tests cover exact full rows/types, duplicate captures, nulls, empty selection,
quoted keys and aliases, both join orders, ON/USING, substring matching, key/byte
bounds, unsupported grammar, stable/prep nonactivation, source snapshot identity,
real selection interruption and subsequent connection reuse. Runtime rollout,
unchanged-query verification through the deployed experimental HTTP endpoint,
and broader performance acceptance remain outstanding. Do not promote to stable
or describe production as fixed on this local implementation alone.

Successful original-query service observations span 9.8–23.0 seconds, all below
the unchanged 120-second production policy. They do not establish p95/p99 or a
20-second guarantee. The final guard changes were replayed with the original SQL;
the 10-row multiset was verified as contained in the full 40-row multiset. Changing
which ten rows appear is permitted because the user query has no ORDER BY.

Private local evidence: `.artifacts/heading-key-scans-probe.log`,
`.artifacts/heading-key-scans-membership.log`, `.artifacts/heading-scope-profile.log`,
`.artifacts/heading-scope-service-probe.log`, `.artifacts/heading-scope-service-trace.log`,
`.artifacts/heading-scope-service-final.log`, and
`.artifacts/heading-scope-service-acceptance.log`. These contain diagnostic plans or
identities and are ignored, not committed. The retained public case is the user's
explicit reproduction. Do not export private query-history records into this report.

Final validation: `make check` passed on the final source (777 backend tests,
34 environment-dependent skips; 24 SDK tests, 6 skips; shared package checks/tests
and public/admin typechecks/builds). Log:
`.artifacts/heading-scope-acceptance-check.log`. Those measurements preceded the combined catalogue release and did not modify production.
