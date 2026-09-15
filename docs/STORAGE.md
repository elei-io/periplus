# Storage

The raw repository supports local disk and S3 through `ingestion/objects/`.
Payloads are immutable and content-addressed. Native rendered HTML uses Zstandard;
Common Crawl imports retain exact response-body bytes in the supported record
subset, plus source headers, location and digests in the capture envelope.
The importer does not preserve the original compressed WARC member byte-for-byte.

Archive objects use [conditional creation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html). Disk publication fsyncs data and its
parent directory; S3 requires conditional `PutObject` support and strongly
consistent key reads. Sixteen contiguous journal shards allow head discovery by
bounded probes rather than a corpus-wide list. Duplicate commit references are
safe because material identities are deterministic and verified.

This layout trades simplicity for metadata I/O: a capture currently creates an
envelope, journal object and commit receipt in addition to its payload. It is
not a demonstrated billion-capture layout. Packed archive segments may become
necessary after measuring object-store metadata cost and write throughput.

ClickHouse stores the disposable material corpus on its own volume. Captures
reference documents, and each document stores one text buffer plus element
offsets; shared body interpretations are not parsed/stored once per visit.
Rebuild capacity must include serving, candidate and retained previous targets,
plus ClickHouse merge space. Storage reporting exposes actual system-table sizes.

Back up Postgres for business continuity, and raw objects + manifest + software
for corpus continuity. NATS persistence improves delivery recovery but is not
an archival backup. A ClickHouse backup speeds restoration but is optional for
reconstructing the retained queryable corpus.
