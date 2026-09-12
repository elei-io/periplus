# Complete positional search

Classification: schema/catalogue and API contract, not an optimizer rewrite.
Status: implementation and isolated validation; no production deployment/rebuild.

Replace prose, content postings and node postings with one term/content posting
containing scalar frequency, document positions and aligned text-node ID lists.
Coverage includes every parsed text-node location. Attribute values and comments
are excluded; ICU word semantics still discard punctuation, symbols and whitespace.
See [the precise coverage/boundaries](../../SCHEMA.md#materialterm-and-materialposting).

The user explicitly chose query-API execution for `search()`, allowing the same
pinned ICU tokenizer at ingestion and query time. The stored macro is a typed
signature for metadata/DESCRIBE and raises an explicit error on direct execution.
Ordinary HTML SQL is unchanged and portable. Output is content ID, representative
snippet/node-ID matches and score. There are no compatibility aliases.

## Correctness and lifecycle

Tests cover inline split words, combining marks, non-Latin text, title/script/style/
template/noscript/foreign text, excluded attributes/comments, empty/nonword text,
structural phrase gaps, repeated phrase words, duplicate captures, parameter
positions, outer joins, preparation without discovery, and phrase filtering before
the result limit. Real DuckLake lifecycle tests cover append-only dictionary
reservation, failed registration, retries, lost receipts, hidden generations and
atomic content replacement with positions/provenance.

Existing registry activation installs the matching catalogue atomically and its
normal finalizer drops unregistered material relations. No new migration path,
service, queue, index extension, cursor or bespoke cleanup was added. The physical
contract is version 12, compiler v11; the registry digest changes, requiring a full
rebuild. No Alembic/control-state schema change is needed.

Validation: 777 backend tests passed (34 skipped), and 24 SDK tests passed
(6 skipped). Full `make check`, including package checks and both frontend builds,
passed. Focused search tests cover large ASCII/Unicode snippets and explicit
inspection behavior.

## Isolated benchmark

Run `benchmarks/query/experiments/positional_search.py` from `packages/periplus` with
`PERIPLUS_DUCKDB_THREADS=2 PERIPLUS_DUCKDB_MEMORY_LIMIT=4GiB`, a retained HTML directory,
`--documents 500`, and an ignored `--report` path. The disposable fixture builds
actual registry projections, runs posting integrity checks, then uses real
QueryService execution under the shared `_measure` runner. Warm repetitions are
ordinary requests, not EXPLAIN ANALYZE, so total staged-search time is included.
No file-scan profile is claimed for this API-level run. Separate physical-layout
evidence remains in [posting-summary](../posting-summary/README.md).

The old body-only experiments are archived under `benchmarks/query/retired/experiments`
and reproduce commit 466beb9. Their completed results do not establish performance
of the new all-text projection. The new fixture bulk-loads its vocabulary; an initial
setup run using per-row insertion was interrupted and is not benchmark evidence.

The 500-document retained sample produced 6,732,328 token occurrences,
149,907 vocabulary entries, 840,034 packed postings, and 1,150,660 nodes.
All posting integrity checks passed. Preparation took approximately 53.5 seconds;
peak process RSS was 3.06 GiB, including parsing, Arrow and DuckDB, and the complete
fixture used 96.8 MB of Parquet (not posting-only storage).

The completed end-to-end fixture build and real query-API benchmark measured:

| Query | Contents | First request | Warm request |
| --- | ---: | ---: | ---: |
| robot | 4 | 212 ms | 192 ms |
| the | 100 | 3,922 ms | 3,911 ms |
| robot the | 4 | 244 ms | 249 ms |
| "artificial intelligence" | 16 | 466 ms | 468 ms |

These include matching, snippets and SQL composition. The service uses its actual
2-thread/512-MB DuckDB configuration; fixture construction uses 4 GiB. One warm
repetition and a local 500-document sample do not establish production latency,
append behavior or billion-capture scalability. In particular, snippets currently
issue bounded per-content/node queries, which deserves profiling for common terms.
Do not increase rebuild batches from these results: complete text coverage adds
substantial preparation memory. Initial benchmark attempts failed on large snippet
nodes and then on sandboxed extension-metadata collection; the current snippet
implementation and separate benchmark-only metadata connection address those failures.

## Release gate

Do not deploy the query-API code against an old active generation. Stop old
materializers through the normal coordinated deployment, build/validate the hidden
new generation, activate its catalogue, then expose the matching query service.
Use existing deployment/rebuild orchestration; do not improvise a mixed-generation
fallback. Benchmark preparation memory before choosing batches: all-document text
and stored occurrence arrays increase work compared with body-only frequencies.
