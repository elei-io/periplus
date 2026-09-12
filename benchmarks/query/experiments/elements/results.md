# Canonical element release validation — 2026-09-12

Application implementation: `2fb53cc`; registry
`bce08130d3dd1f9b4370a7a77ac4708767f243d1351d3febbb8f41db37058637`.
This is schema/catalogue work; no API text-predicate rewrite participates.

## Complete local batch pipeline

500 retained HTML documents, shuffled into ten batches of 50. Actual ingest
visits/documents, pinned source reads, ownership selection, first-write intent,
preparation, Parquet registration and receipt replay. Two parser processes;
DuckDB two threads, 4 GB memory and 8 GB spill allowance. Operational state is
isolated SQLite; immutable compressed objects are local, not production S3.

- 594,873 elements, 460 JSON-LD rows, 58,351 links, 500 readiness rows.
- 20.18 seconds through preparation/publication; 19.78 seconds preparation and
  Parquet, 0.40 seconds committing the ten batches.
- 42.15 MB compressed output across all projections.
- Parent-process peak RSS 466.5 MB; this excludes parser-child RSS.
- Every projection's validation queries passed. Applied-batch replay skipped work.

These are local measurements, not predicted production throughput. They exclude
NATS delivery, production metadata latency, remote object latency and concurrent
worker contention. The retained fixture's content IDs occupy a narrow prefix;
it is not a billion-document distribution test.

## Production maintenance image

Used the exact pinned Linux/amd64 LakeDucktor image:
`ghcr.io/elei-io/lake-ducktor@sha256:4ed6020562d906d1fb679c2125b776c0bfa378fadd8890310b892e27110a0841`.
The local container had two CPUs, 5 GB total memory, 4 GB DuckDB memory and no
network. It loaded that image's patched DuckLake, not the stock extension.

Native sorted compaction reduced 80 element files to eight in 1.74 seconds,
preserving the complete row count. A companion publication fixture verified
identical full content-query result digests, with files read falling from nine
to one. Adding 80 more contents and compacting again succeeded (16 files to eight,
1.91 seconds); the original content's result stayed identical and read one file.

In that companion fixture, warm content lookup was 5.8 ms before compaction and
22.5 ms afterward. Fewer files did not mean lower elapsed time on this small local
workload. Larger row groups, caching and the different file layout affect latency.
Broad text discovery remains a full text scan. There is no claim that compaction
alone provides a search index or settles long-term maintenance throughput.

Stock DuckLake revision d8a1881e rejected external per-batch Hive paths. Production's
existing external-Hive/sorted-compaction patches handled them correctly. No writer
path workaround or extension change was introduced into Periplus.

## Nested-page preparation

A 1 MiB text value inside 256 div wrappers produced 260 elements and 272.7 MB of
Arrow intermediates through actual preparation. It completed in 1.60 seconds,
with 377.7 MB peak RSS in the sequential test process. The per-document output
limit is now 1 GiB; the batch limit remains 8 GiB. Source-size limits and worker
concurrency still apply. This fixture does not establish a bound for all malformed
or extremely deeply nested documents.

## Application checks

`make check` passed: 760 backend tests (34 environment-dependent skips), 24 SDK
tests (six skips), package/public tests, and both frontend type checks/builds.
Coverage includes exact nested text, whitespace, Unicode, scripts/styles/titles,
templates, options, foreign namespaces, list/table exclusions, source identities,
uncertain receipts, rollback, retirement and generation activation.
