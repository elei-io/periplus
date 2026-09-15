# Evidence and reproduction

Companion to [DECISION.md](DECISION.md). Measurements were collected on 2026-09-15. These are deliberately small decision experiments, not a production implementation or billion-object capacity benchmark.

## Production deduplication measurement

[measure_production.py](measure_production.py) ran a separate one-thread DuckDB process using the existing production query pod’s SELECT/GetObject-only credentials. It loaded already installed extensions, attached read-only, used a 512 MB memory limit with spill disabled, a 20-second query interrupt and a 45-second process watchdog. No services, policies, database contents or raw objects were changed. The query completed in **1.2373 seconds**, including attachment/setup, at **2026-09-15 03:22:53 UTC**.

It joined `ingest.documents` to `ingest.visits` on visit identity and grouped by content hash and HTML classification. It did not count fulfillments as extra physical captures. The resulting joined population contained HTML only; it excludes visits without a retained document and any unjoined document rows. One read transaction covered the grouped measurement. No precise DuckLake snapshot ID was recorded for this run.

| Quantity | Result |
| --- | ---: |
| Joined captures with retained HTML | 185,190 |
| Distinct content hashes | 176,750 |
| Extra identical-body captures | 8,440 (4.5575% of captures) |
| Logical bytes counting every capture | 71,795,576,328 |
| Logical bytes counting every content once | 70,312,970,582 |
| Compressed bytes counting every capture | 9,540,232,901 |
| Compressed bytes counting every content once | 9,308,549,812 |
| Compressed savings | 231,683,089 bytes (2.4285%) |
| Repeats within the same requested URL | 5,610 |
| Additional requested URLs sharing an existing hash | 2,830 |
| Compressed savings attributed to within-URL repetition | 145,340,811 bytes |
| Compressed savings attributed to cross-URL repetition | 86,342,278 bytes |
| Contents occurring more than once | 6,278 |
| Contents shared across requested URLs | 1,473 |
| Conflicting logical sizes under the same hash/classification | 0 |
| Different stored sizes under the same hash/classification | 0 |

The within-URL/cross-URL decomposition is arithmetic, not a causal simulation of a different crawler freshness policy. It uses `n - distinct_requested_urls` and `distinct_requested_urls - 1` per content. It does not determine whether a revisit happened inside a particular reuse window. Repeated URLs may legitimately need a fresh observation after that window. Different requested URLs can also include aliases/redirects; these are not necessarily unrelated sites.

The bytes use existing `stored_bytes` metadata rather than recompressing the entire production corpus. Since no size variants were found within content groups, the decomposition is exact for these recorded compressed sizes. It excludes object metadata, filesystem allocation, replicas, backups, capture journals and materialized tables. The percentages cannot be applied unchanged to future Common Crawl imports.

Machine-readable result: [production.json](../../../.artifacts/raw-archive-20260915/production.json).

## Local comparison inputs

The saved sample at `/Users/ekku/Code/elei/periplus/.artifacts/material-architecture/production-sample-direct` contains 256 distinct public HTML bodies, selected during the earlier investigation using hash-ranked content selection at snapshot **426727**. Each was originally downloaded and hash-verified. This experiment again decompressed every object and verified its full SHA-256 and byte length.

- Logical sample bytes: **95,051,636**.
- Manifest SHA-256: `870bbbd16a5f3f8cf86ba89420d4ead3eff91c4c2099efa6df222d23684a4abc`.
- Zstandard level 6 body bytes: **13,863,398**.
- gzip level 6 body bytes: **16,529,989**, 19.23% larger in this sample.
- Python **3.14.7**, macOS **26.5.1 ARM64**, `warcio==1.8.1`.

The sample is uniform over the selected distinct-content population, not weighted by production revisit frequency. Native capture envelopes in the fixture are intentionally small and synthetic, with URLs under `fixture.invalid`, timestamps, representation, outcome, provider identity and duplicate header pairs. They are not complete production attempt/step envelopes. Do not use their compressed journal size to price a full future capture schema.

[proof.py](proof.py) compares:

**A. Content-addressed payloads:** Zstandard-compressed body per hash, plus immutable JSON capture records. A refinement writes those same capture records in compressed journals of 64 records, leaving payloads unchanged.

**B. Packed WARC:** small self-contained WARC objects first, then a packed archive with each body stored once. The prototype represents bodies as WARC `resource` records and captures as linked Periplus JSON `metadata` records. It is valid WARC with an application metadata profile; it is not an implementation of Common Crawl-style HTTP replay. Global cross-file reference behavior is tested separately. Compression is record-at-a-time gzip.

Both preserve the same fixture captures and exact payload bytes. Packing writes a durable replacement receipt and removes old files. Recovery starts with no saved lookup index and no database/queue state. The fixture’s in-memory dictionaries fit in RAM; production recovery must stream/batch and use disk-backed indexes as needed.

## Local S3 I/O observations

The bounded S3 run used the first 64 sample bodies and two synthetic captures per body: 128 captures. It wrote only below a fresh random `diagnostics/raw-archive-proof/<uuid>/` prefix on the MacBook’s existing localhost S3 service. It did not create a bucket, change bucket policy or touch application prefixes. Cleanup listed and deleted that prefix and verified it empty.

| Operation | Seconds | Data GETs | PUTs | Data read | Data written |
| --- | ---: | ---: | ---: | ---: | ---: |
| A: create payloads + individual capture records | 0.6897 | 0 | 192 | 0 | 3,251,022 B |
| A: recover individual records + payloads | 0.4774 | 192 | 0 | 3,251,022 B | 0 |
| A: recover grouped journals + payloads | 0.1715 | 66 | 0 | 3,200,389 B | 0 |
| B: create small WARC objects | 1.6559 | 0 | 128 | 0 | 7,804,535 B |
| B: pack, verify and retire inputs | 1.4289 | 129 | 2 | 11,742,133 B | 3,944,334 B |
| B: recover packed archive + receipt | 0.1054 | 2 | 0 | 3,944,334 B | 0 |

The compaction row additionally deletes 128 objects. LIST operations are separately recorded in JSON: 2 for A’s individual recovery, 1 for grouped recovery, 4 for packed recovery and 7 for compaction. Failed/redundant attempts and production dedupe discovery are not modeled in these timing rows; the fixture supplies the distinct-body set. Conditional-create conflict behavior is tested separately against real local S3.

Thirty-two verified random content lookups, serially:

| Layout | Median | Observed p95 | Bytes read across lookups |
| --- | ---: | ---: | ---: |
| Individual payload | 2.20 ms | 3.28 ms | 1,573,191 |
| Packed WARC with known ranges | 3.14 ms | 5.32 ms | 1,884,290 |

Packed lookup timings assume a rebuilt index is already available; they exclude index bootstrap. The very small sample, warm cache, fixed execution order, local network, single requester and concurrent ordinary desktop/Compose activity preclude capacity or tail-latency claims. The structural request counts are more useful than small timing differences.

For 1,024 synthetic captures over the same 256 bodies, the filesystem run wrote **66,393,094 B** of small WARC objects and **17,074,971 B** of packed output plus receipt. CAS with individual capture records wrote **14,345,578 B** once. Grouped journals plus bodies occupied **13,939,292 B**. This illustrates temporary/write amplification under deliberate repetition; it does not estimate the current production duplication ratio.

The gzip/Zstandard difference is a codec comparison, not proof that WARC intrinsically costs 19% more. Alternative WARC compression or different metadata grouping could change byte counts. It would not remove the dependency and selective-rewrite issues.

## Selective eviction experiment

[deletion_cost.py](deletion_cost.py) uses the same 256 bodies with two captures each. It creates eight WARC groups of 32 distinct bodies. Selection deliberately chooses one body from each group; the first selected body is marked protected by an active collection. Seven bodies and their 14 captures are eligible. The selection is dispersed by construction, not a forecast of actual SQL predicates.

| Operation | Individual payloads | Packed WARC |
| --- | ---: | ---: |
| Payload/archive files deleted | 7 | 7 |
| Replacement archives written | 0 | 7 |
| Object reads | 0 | 14 |
| Object writes, including tombstones/receipts | 14 | 28 |
| Bytes read | 0 | 29,415,954 |
| Bytes written | 742 | 14,556,077 |
| Net bytes reclaimed | 282,962 | 306,016 |
| Local elapsed time | 0.0136 s | 3.3691 s |

Both pass object-only recovery afterward: the protected content survives, selected eligible bodies are physically absent, and their captures do not reappear. Different reclaimed sizes reflect codec and metadata differences. These timings exclude SQL selection, live reference/protection scans, admission fencing, query-table deletion, backups and object-store version cleanup. The CAS zero-read cell is payload-rewrite I/O only; deletion eligibility is not free.

The experiment models a static protection set. It does not prove that a real collection becoming active concurrently will be handled safely. That remains an explicit release gate.

## Failure and integrity evidence

The final proof passed 14 checks:

| Check | What was exercised |
| --- | --- |
| Process death after packed output | A subprocess exits with `os._exit(31)` before its receipt; original files remain authoritative and recoverable. |
| Process death after replacement receipt | Exit 32; clean recovery selects the replacement while old objects still exist. |
| Process death during old-file deletion | Exit 33; clean recovery succeeds and retirement resumes. |
| Reader drain | A modeled active-reader flag prevents old-file deletion; those files remain readable until release. This is not a distributed lease test. |
| Shared-content deletion, CAS and WARC | Deleting one capture preserves a body needed by another. Deleting all its references permits physical reclamation; tombstones survive clean recovery. |
| Missing body | A retained reference to absent payload causes failure, not silent omission. |
| Conflicting capture identity | Different envelope bytes under the same conditional object key are rejected. |
| Queue/index loss | An archived capture survives reconstruction with no queue or saved lookup index. |
| Orphan payload | Uploaded bytes without a committed capture do not invent a visit. |
| Repeated packing | Packing an already identical output cannot retire that output as its own input. |
| Cross-archive references | Later metadata-only archive resolves earlier body records even when listing order is reversed. |
| Missing referenced archive | Removing the body-bearing archive makes recovery fail. |
| Wrong bytes under a correct key | Digest validation detects a valid compressed object containing the wrong payload. |

The checks use actual temporary files, maintained WARC serialization/parsing and real child-process death. Filesystem publication uses a completed temporary file plus no-overwrite hard link. This proves process-crash behavior, not power-loss/filesystem durability. S3 separately passed conditional conflict rejection and range-read integrity checks. No production workers or live queries were killed.

The local proof caught a self-retirement hazard: packing a single archive into identical bytes must be a no-op, otherwise a replacement receipt naming the same input/output can make the active set disappear. That is now covered by a negative-regression assertion.

## Real Common Crawl compatibility check

[fetch_common_crawl.py](fetch_common_crawl.py) selected the then-latest published index, **CC-MAIN-2026-34**, and fetched one bounded HTTP byte range for each of two public URLs. The download required status 206 and an exact declared range length. No whole crawl or large WARC was downloaded. The index’s filename/offset/length fields support this retrieval method. [Common Crawl index documentation](https://commoncrawl.org/url-index).

| URL in record | Original capture time | Compressed record bytes | Archived body bytes |
| --- | --- | ---: | ---: |
| `https://example.com/` | 2026-08-07 10:44:56 UTC | 953 | 559 |
| `https://www.iana.org/domains/reserved?` | 2026-08-09 10:51:11 UTC | 3,972 | 10,499 |

[check_common_crawl.py](check_common_crawl.py) validates the WARC digests, separates the exact archived HTTP header block from body bytes, derives a stable provider/dataset/record-based capture identity, and exports through `warcio`. Both exported HTTP blocks match byte-for-byte and their WARC capture times remain unchanged. Exported gzip bytes and WARC header serialization are not claimed identical to the downloaded container.

The initial high-level writer path changed header serialization and failed exact-block equality. The corrected path preserves the raw HTTP header block. Synthetic cross-URL revisit records referencing each real record resolve successfully; unresolved references are rejected. This is a small adapter-level reference test, not a complete revisit resolver.

Missing coverage includes encoded/chunked/truncated bodies, malformed archives, duplicate WARC IDs with conflicting evidence, segmentation, linked request/metadata records, provider variations, the deployed importer, and large streaming imports. Do not promote two successful examples into a claim of complete archival compatibility.

## Reproduce without changing the application

From `/Users/ekku/Code/elei/periplus-clickhouse`:

```sh
uv run --no-sync --with warcio==1.8.1 --project packages/periplus python \
  docs/experiments/raw-archive-20260915/proof.py \
  --sample /Users/ekku/Code/elei/periplus/.artifacts/material-architecture/production-sample-direct \
  --output .artifacts/raw-archive-20260915/proof.json --s3-env .env

uv run --no-sync --with warcio==1.8.1 --project packages/periplus python \
  docs/experiments/raw-archive-20260915/deletion_cost.py \
  --sample /Users/ekku/Code/elei/periplus/.artifacts/material-architecture/production-sample-direct \
  --output .artifacts/raw-archive-20260915/deletion.json

uv run --no-sync --project packages/periplus python \
  docs/experiments/raw-archive-20260915/fetch_common_crawl.py \
  --output .artifacts/raw-archive-20260915/common-crawl

uv run --no-sync --with warcio==1.8.1 --project packages/periplus python \
  docs/experiments/raw-archive-20260915/check_common_crawl.py \
  --input .artifacts/raw-archive-20260915/common-crawl \
  --output .artifacts/raw-archive-20260915/common-crawl-proof.json
```

Omit `--s3-env` for a temporary-files-only run. The S3 mode rejects non-localhost endpoints and cleans only its own random prefix. The historical private sample is not committed; retain its manifest and verified objects to reproduce the same numbers. Re-fetching Common Crawl’s latest index later may select a different crawl. The saved source manifests contain exact URLs, byte ranges and record SHA-256s for reproducing these particular inputs.

To repeat the bounded production measurement, use the supported homelab operator wrapper:

```sh
make -C /Users/ekku/Code/homelab run CMD="python /Users/ekku/Code/elei/periplus-clickhouse/docs/experiments/raw-archive-20260915/measure_production.py --output /Users/ekku/Code/elei/periplus-clickhouse/.artifacts/raw-archive-20260915/production.json"
```

Scripts and documents are isolated to this experiment directory. Runtime source, dependency manifests, Compose, git branches and production configuration were left untouched. No deployment, automated cron, production retention or migration was performed.
