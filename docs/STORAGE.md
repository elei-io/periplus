# Storage

The raw repository supports local disk and S3 through `ingestion/objects/`.
Payloads are immutable and content-addressed. Native rendered HTML uses Zstandard;
Common Crawl imports retain exact response-body bytes in the supported record
subset, plus source headers, location and digests in the capture envelope.
The importer does not preserve the original compressed WARC member byte-for-byte.

Archive objects use [conditional creation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html). Disk publication fsyncs data and its
parent directory; S3 requires conditional `PutObject` support and strongly
consistent key reads. The next immutable metadata segment is the capture commit
point. Each segment contains up to 64 complete observations or retirement events,
bounded to 8 MiB expanded JSON. Payloads remain separate content-addressed Zstd
objects. No capture envelope or receipt file is written separately.

Sixteen contiguous segment journals support logarithmic head discovery without
listing the corpus. Range directories contain at most 4,096 segments each.
Logical event positions remain dense even when segments are only partly full.
The frontier relay claims up to 64 deliveries and batches their observations;
idle polling is bounded to one second. Low-rate imports publish immediately.
Partial batches are normal, especially when UUIDs span multiple shards.

Exact capture-ID conflict checks use a process-owned, disposable SQLite lookup
reconstructed from the journal. Its pager cache is bounded to 8 MiB and its
records spill to a temporary disk database. Conditional segment creation settles
competing writers: a loser applies the winner before checking its identities
again. A lost reply recovers the original position, without another receipt file.
The cache is never a required backup or a Postgres corpus table.

A cold publisher must replay metadata to warm its lookup before publication.
That startup cost and local index disk usage grow with the corpus; persistent
derived checkpoints are not implemented. Direct batch locators and sequential
rebuild readers bypass the index. This is a launch contract, not certification
of billion-capture startup or rebuild performance. Separate bodies are the chosen
layout; a future physical packing migration would be a deliberate operation.

ClickHouse stores the disposable material corpus on its own volume. Captures
reference documents, and each document stores one text buffer plus element
offsets; shared body interpretations are not parsed/stored once per visit.
Rebuild capacity must include serving, candidate and retained previous targets,
plus ClickHouse merge space. Storage reporting exposes actual system-table sizes.

Back up Postgres for business continuity, and raw objects + manifest + software
for corpus continuity. NATS persistence improves delivery recovery but is not
an archival backup. A ClickHouse backup speeds restoration but is optional for
reconstructing the retained queryable corpus.
