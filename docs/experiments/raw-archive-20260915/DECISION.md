# Periplus raw evidence: a durable foundation

2026-09-15 · Decision recommendation, supported by isolated experiments.

**Use globally content-addressed payload objects and small immutable evidence journals in object storage. Support WARC at the import/export boundary. Do not make a daily/weekly/monthly hierarchy of deduplicated WARC packs the primary mutable corpus store today.**

The deciding requirement is routine, SQL-driven physical eviction of unhelpful content while preserving active collection interests. Individually addressable payloads make this substantially cheaper and simpler. Packed WARC remains a credible alternative for a largely retained archive, and its sequential-read advantage is real.

In practical terms: each distinct HTML body gets one compressed file. Small journal files record who captured it, its URL, the time and its content hash. Ten visits to identical HTML produce ten observation records sharing one body file. When SQL selects that content for eviction and no collection protects it, we can remove its body file without rewriting other pages. A durable deletion record prevents old observations from returning during recovery. WARC import fills these same records; export assembles them back into archives.

This is a storage design recommendation, not an implemented replacement or a production-readiness claim. Application code, running services and production configuration were not changed by this investigation. [Evidence, limitations and reproduction](EVIDENCE.md) accompany the decision.

## 1. What the system promises

After Periplus reports evidence as **archived**, the bucket alone contains enough information to reconstruct its retained captures, exact payloads and immutable lineage. Starting with empty ClickHouse, Postgres and NATS must not lose that archived corpus. Restoring current accounts, payments, policies and unfinished crawler execution still requires operational backups.

An archived capture records an observation. A payload is a byte sequence that many observations may share. A collection has interests in observations; it is not the physical owner of shared bytes. ClickHouse serves derived analytical representations. NATS delivers work. Neither is the sole durable home of capture timestamps or metadata.

The user’s deletion requirement is part of this contract: an operator selects low-value content using SQL; authoritative current collection protection can veto removal; approved removal eliminates the corresponding retained evidence, analytical material and eventually the actual payload bytes. A rebuilt corpus must not resurrect evicted observations.

## 2. Evidence that drove the decision

| Observation | Consequence |
| --- | --- |
| A bounded read-only production query found 185,190 joined HTML captures and 176,750 distinct bodies. Compressed payload bytes fall from 9,540,232,901 to 9,308,549,812 with exact deduplication: 2.43% saved. | Keep exact content identity and deduplication, but do not assume enormous present-day savings. Recrawling and overlapping imports may change the ratio. |
| In the saved 256-content sample, Zstandard level 6 used 13.86 MB; gzip level 6 used 16.53 MB for the same bodies. | Codec choice affects storage independently of packing. WARC itself does not intrinsically impose this difference. |
| Local S3 recovery for 64 bodies and 128 synthetic captures took 0.1715 s with grouped journals plus payload objects, versus 0.1054 s with one packed WARC plus its replacement receipt. Reads were 66 GETs versus 2 GETs. | Packing reduces request overhead. Individual payload objects have a real object-count/recovery cost. These are tiny serial warm-cache observations, not capacity estimates. |
| Evicting seven dispersed unprotected contents from eight packs of 32 contents required 29.42 MB read and 14.56 MB written to reclaim about 0.31 MB. Separate payloads required seven deletes and 742 bytes of tombstones to reclaim about 0.28 MB. | Routine selective eviction is a strong reason to keep bodies independently addressable. Selection/reference checking costs are excluded equally from both measurements. |
| Two real Common Crawl records passed local import/export checks preserving original capture time, exact HTTP header block and exact archived body. | We can support archival interchange without adopting the provider’s physical layout as our primary store. This is a narrow format proof, not a completed importer. |

All MB figures above are decimal. The duplicate-heavy fixture repeats the same 256 real bodies four times deliberately; it is not a measured production revisit distribution. Detailed inputs, exact numbers and failure tests are in [EVIDENCE.md](EVIDENCE.md).

## 3. The permanent contract

Freeze the meaning of these records before moving more ingestion into the new system. Do not freeze filenames or a packing calendar as public identities.

### Payload

- `sha256`: hash of the exact retained, uncompressed byte sequence.
- `byte_length`: verified length of those bytes.
- The stored object may be compressed; its compression is separate from the payload identity.
- No URL, capture time, collection ID or parser revision participates in byte identity.
- Exact identity is not semantic similarity. Changing a timestamp inside HTML creates different bytes.

Representation and interpretation belong to the capture descriptor. Identical bytes can be physically shared even when their interpretation differs; a materialization cache must additionally account for representation, decoding rules and parser build.

### Capture envelope

A versioned, typed envelope includes:

| Field group | Required meaning |
| --- | --- |
| Identity | Stable `capture_id` and a digest of the frozen envelope; retries preserve both. Conflicting evidence under one capture ID fails validation. |
| Observation | Original requested/effective URLs, capture time with known precision, outcome and completeness/truncation information. Unknown values stay unknown. |
| Payload reference | Optional payload hash and length, representation and decoding information. Failed/empty captures can have no payload. |
| Acquisition evidence | Available HTTP evidence, attempts and completion steps needed to recreate existing `ingest.*` facts. Preserve duplicate headers and original HTTP header bytes where supplied. |
| Provenance | Provider, dataset, original record ID and source location/checksum where available. Import time belongs to an import/admission event rather than redefining original capture time. |
| Lineage | Frozen dispatch reasons and stable references needed to recover collection associations. Later reuse appends an association; it does not edit the original capture. |

For native captures, generate identity once before durable publication and reuse the frozen envelope. For imported captures, use a specified deterministic mapping of provider, dataset and original record identity, with conflict checking. Do not let every import invent a new visit or a new observation time.

Periplus currently stores browser-produced UTF-8 DOM snapshots. Label them `rendered_dom_utf8`. They are not original HTTP response bodies. WARC imports retain a separately identified HTTP-body representation and its exact source headers; decoding is an explicit transformation for materialization. A later importer must test transfer/content encodings rather than silently conflating decoded text with archived bytes.

### Other immutable facts

Journal the existing immutable collection definitions/outcomes, fulfillment/reuse associations and acquisition reasons needed to reconstruct analytical lineage. This is a small enumeration of Periplus evidence records, not a general event-sourcing framework for the whole application.

Version the archive record format independently of `public_v*`. A public SQL schema change or parser rebuild does not rewrite raw evidence. Preserve source evidence and implement a deliberate decoder when an archive format evolves; do not silently reinterpret old records.

## 4. Physical layout and the write boundary

Proposed layout:

```text
raw/v1/payloads/sha256/<prefix>/<hash>.zst
raw/v1/evidence/<provider>/<dataset>/<upload-date>/<segment-id>.jsonl.zst
raw/v1/evictions/<operation-id>/<batch-id>.jsonl.zst
```

Content is globally addressed, independently of provider or collection. Evidence segments contain complete frozen envelopes. Eviction records contain explicit identities and decisions that every replay must honor. Indexes and checkpoints live outside the authoritative `raw/` namespace and are reproducible.

Use independently compressed payloads and bounded compressed JSON Lines journals. JSON keeps the authoritative metadata directly inspectable; Parquet is useful as a derived scan/index format. Do not make a single continually rewritten `known_content.parquet` an ingestion dependency. A payload’s initial location follows directly from its hash, so normal reads and duplicate checks need no global location catalogue.

The native write sequence is:

1. Freeze the complete evidence envelope, including stable identities.
2. Write or adopt the payload at its content address using conditional creation, and verify the payload identity. Keep it protected from reclamation until the evidence is durably indexed for retention.
3. Write a complete evidence segment containing the envelope. **This completed segment is the archival commit.** All referenced payloads must already exist.
4. Publish or reconcile NATS delivery. The existing ingestor may own journal publication before its ClickHouse append; no separate archive service is needed merely for this design.
5. Append verified evidence and materialize through the existing pipeline. Keep retry/delivery markers bounded and distinguish archived from ingested/query-ready.

Start with a proposed maximum 30-second journal age and bounded record/byte limits; these are configuration starting points, not benchmarked SLOs. A partially filled segment is closed on time. A worker must keep enough buffering or durable pending-work state to retry until commit. Losing every store before step 3 can lose unarchived work: do not report it as archived or promise zero-loss capture before this boundary.

Amazon S3 publishes complete objects, and multipart upload can assemble a new object before completion. It does not provide a transaction across a payload and journal. Writing dependencies first gives a safe asymmetry: a crash may leave an unreferenced payload, but must not leave a committed envelope referring to missing bytes. Conditional-write and consistency behavior must be verified against the actual compatible backend. [AWS PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html), [multipart upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html).

Different envelopes may be replayed in a different order or segment grouping. Capture identity and envelope digest, not segment identity, govern logical idempotence. Identical copies collapse; conflicts fail closed and are reported. After a lost upload response, reconcile the immutable key and checksum before retrying publication.

## 5. Recovery with no databases

Recovery runs against a fixed inventory of completed evidence and eviction objects. Pause physical raw reclamation while this inventory is in use; normal archival appends can continue and be picked up in a catch-up pass.

1. Read and validate eviction records. Build an exclusion set that cannot be lost with a query database.
2. Read evidence journals; reconstruct captures and lineage, suppress evicted identities, reconcile identical retries and reject conflicts.
3. Derive the live payload set from retained references. Missing live payloads or checksum mismatches stop validation; orphan payloads do not create observations.
4. Recreate native ingestion tables, lookup indexes and materializations using retained bytes and the selected parser build.
5. Catch up new journals and evictions, recheck exclusion before publication, then activate the rebuilt analytical output.

The archive inventory is a recovery work plan, not a second permanent truth. It must be complete for its stated boundary. A date prefix is an efficient enumeration aid, not proof that all earlier uploads or late imports have been processed. Daily reconciliation must also pick up missed work after downtime.

Runtime progress, NATS consumers and Postgres receipts can be recreated. They are not required to identify which captures exist. If operational protection state cannot be restored, keep corpus eviction disabled; do not guess that missing collections authorize deletion.

## 6. SQL-driven corpus eviction

The SQL query selects candidates. It never grants deletion authority by itself. Joins or counts used to select low-value content may be stale by the time a worker acts.

For each bounded candidate group:

1. Resolve candidate content to its retained captures and current collection interests. Report selected, protected and eligible content separately, with actual stored bytes.
2. Reserve the exact content/observations through the same ownership boundary used by archive publication and collection attach/reuse. Recheck current active collection protection, any explicit retention promise, pending publications and readers. A new protected interest wins over eviction until the decision is durably committed.
3. Persist explicit eviction records in object storage before destructive changes. This is the irreversible evidence decision; subsequent steps are retryable work. The records suppress the affected capture IDs even if old journal files or old NATS messages are replayed.
4. Exclude those captures and their contributions from public query results, corpus seed selection and materialization. Remove content-owned material only after no retained capture needs it. A background rebuild must consume these exclusions before activation.
5. Once no retained reference or in-flight protected operation needs the payload, delete the payload object and verify the outcome. Record completion; metadata cleanup can follow separately.

No Postgres transaction spans remote object I/O. A bounded exact claim can coordinate live operations, while the durable eviction record remains recoverable from S3. Lease expiry alone is not proof that a remote write/delete has stopped. After an ambiguous destructive request, keep that content closed to reuse until completion is established, or use a backend-supported version/fencing mechanism. Do not silently recycle ownership and risk a stale delete removing newly referenced bytes.

The first implementation must prove these races with actual workers before enabling destructive maintenance. The experiment verifies the data/reference outcomes under explicit quiescence; it does **not** establish distributed collection-admission or raw-reader fencing.

Deleting an observation is different from permanently banning a URL or payload. This recommendation allows a genuinely new authorized capture later, with a new capture ID, while blocking resurrection of the retired observation. Removing stored links prevents the old corpus from contributing those links to seed/selection SQL. It does not prevent a live page from linking to the same URL again. Persistent crawl avoidance, if wanted, belongs in an explicit crawler policy, not in an accidental missing-object behavior.

The precise protection policy remains product-owned: active collection requests always protect; completed collections protect only for any explicit retained-result promise. This document does not reinterpret existing indefinite-retention contracts as expired just because execution completed.

## 7. WARC interoperability

WARC is a good exchange format. It has resource/response records, linked metadata and revisit records for repeated content. Record-at-a-time gzip supports indexed range reads. Those features make packed archives practical, but do not implement global ownership, safe reclamation or Periplus collection semantics. [WARC 1.1](https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/).

Import through a bounded WARC reader: preserve provider identity and observation metadata, validate digests and limits, resolve revisits, and publish the same native payload/envelope contract. References outside the imported set must be resolved before archival success. A source URL alone is not a durable copy. WARC/WAT/WET have different contents; WARC is the source for reconstructing HTML evidence. [Common Crawl formats](https://commoncrawl.org/get-started).

Export retained captures as standards-conforming records with the right representation and provenance. A full raw-database rebuild uses the native archive decoder; generic WARC tools can read exported archives. Do not claim the native JSON journal is WARC or that a DOM snapshot is a complete replayable website.

The real-record proof preserves the exact archived HTTP header block and body. Re-serializing headers through a dictionary or a high-level writer changed bytes in an initial test, so the successful path preserved the exact block. This does not promise identical original gzip bytes or every provider-specific WARC feature. [warcio](https://github.com/webrecorder/warcio) is the maintained library used in the experiment.

## 8. Why not calendar-based global WARC packing now?

The proposed hierarchy is feasible, but it makes cheap corpus deletion dependent on an archive rewrite engine and a durable content-location catalogue. Global references also make expiry of a whole provider/month unsafe unless all incoming references are accounted for. The date on a file does not establish ownership of its payloads.

A single compactor would need frozen input inventories, verified replacements, durable receipts, reader draining, replay exclusions and complete reference preservation. Those are justified for a predominantly retained archive. They are a significant burden for a corpus deliberately pruned by arbitrary SQL predicates.

If packing is introduced later, act on size and measured benefit; use yesterday/last week/last month only to choose closed candidates. Never rewrite already well-sized payloads automatically merely because a larger calendar interval ended. Pack metadata journals independently if their object count becomes material; that does not move shared bodies or require a global body-location map.

The rejection is about this workload and the current evidence, not a claim that packed WARC cannot work. The benchmark gives it a real sequential-read advantage. An append-only retained external archive may deserve different physical treatment later, but do not introduce two authoritative storage paths now without an active caller and a common verified recovery contract.

## 9. Implementation and acceptance

Implement in this order:

1. Add the typed, versioned raw envelope and bounded journal publication to the ingestion boundary. Preserve the existing content-addressed payload mechanism; make stored observation metadata independently recoverable.
2. Add an object-only recovery command and a bounded WARC import/export path. Test real headers, encodings, truncation, failures, revisits and conflicting identities.
3. Add SQL candidate planning, collection protection rechecks and durable eviction records; connect them to query visibility and rebuild publication.
4. Prove destructive reclamation with real ownership/reader races and ambiguous I/O responses before enabling it. Keep physical deletion visibly disabled until this gate passes.

Release gates:

- A clean-store recovery reproduces exact retained capture/envelope identities and payload digests, including failed captures, shared collections and imported evidence.
- Crash after payload upload, after journal commit, after tombstone commit and after raw deletion preserves the specified outcome under retry.
- Replaying old jobs/journals cannot resurrect evicted captures; a new active interest racing selection prevents unauthorized deletion.
- A rebuild or raw download cannot lose its input during reclamation; stale workers cannot publish or delete after ownership loss.
- Evicted material/links cannot return through a background rebuild. Protected content remains available.
- All mutations in tests are isolated to local Compose or disposable storage; no production rollout is implied.

**Remaining capacity gate:** one object per distinct payload means one billion distinct payloads means one billion objects. This investigation did not test the NAS/backend at that cardinality. Before claiming that scale, test object metadata footprint, listing/index bootstrap, GET/DELETE throughput and restore time on a disposable representative backend. The small local S3 benchmark cannot settle it. Keep application contracts expressed in payload identity so a future measured placement change does not change capture IDs or force recrawling.

Object-store durability also depends on the deployment’s actual storage and independent backups. This design removes database dependence from corpus recovery; it does not make a single NAS immune to loss.

The decision can be made now: **retain exact global content identity, make capture evidence independently durable in S3, and favor independently deletable payloads for the SQL-pruned corpus.** The destructive-concurrency and high-cardinality gates are explicit implementation obligations, not hidden assumptions.
