# Archive layout experiment

This directory compares physical archives without changing the production storage
contract. Results and the deployment recommendation belong in `REPORT.md`.

## What is compared

| Layout | Payload | Capture facts | Discovery and index |
| --- | --- | --- | --- |
| Separate | Existing content-addressed Zstandard object | Production `Archive` envelope | Production sharded journal and receipt |
| Packed CAS prototype | Individually Zstandard-compressed framed records | Exact capture JSON | Per-pack derived index and immutable commit |
| Packed WARC prototype | WARC 1.1 resource records, independent gzip members; revisit records for repeats | Exact capture JSON in metadata records | Per-pack derived index and immutable commit |

The default pack boundary is 64 MiB of uncompressed unique payload or 256
captures, whichever comes first. An oversized record gets a larger pack. Calling
`ingest` flushes its final pack; the low-rate test flushes every capture. There is
no unmeasured buffering period hidden inside the ingestion result.

Both packed implementations are **single-writer experiment code**, with an
in-memory locator map. They are not production candidates ready to deploy.
`--workers 4` tests four disjoint body-hash owners writing immutable packs into
one target. It does not prove overlapping delivery, owner reassignment or fencing.
Packed metadata is deliberately self-describing, even when this repeats facts
between the record and its derived index. WARC uses resource records for rendered
HTML; it does not invent an HTTP response or headers that were never captured.
The fixed WARC packaging date is separate from the original observation timestamp
in the capture envelope. The custom WARC header is an experiment's index-recovery
aid; standard readers can still consume the resource and metadata records.

## Data and measurement boundaries

`lab_inventory.py` runs read-only against the **old deployed query image**, pins a
DuckLake transaction and exports current visits joined to documents. It does not
perform retention/adoption decisions. `dataset.py` maps those frozen facts to
current capture envelopes. Fingerprints establish equivalence of those envelopes,
not of acquisition evidence that the old system never stored.

The `representative` selection is actually a **stress sample**: deterministic
random selection augmented with the largest pages and repeated-body candidates.
Do not extrapolate its byte average to the whole corpus. `codec.py` separately
uses a deterministic random sample; `repeated` is explicitly synthetic.

`run.py` downloads and verifies fixtures before timing. Ingestion is **archive
adoption**: separate objects reuse existing compression; packed candidates decode
and recompress it. `codec.py` measures fresh-body compression cost separately,
excluding fixture reads and decoding. Every codec output is round-trip checked.

Stored bytes include all payloads, capture metadata, indexes, journals and commit
objects written by each layout. Common software bundles are excluded because they
are identical for all layouts. S3 object lengths do not measure ZFS allocation,
filesystem metadata, replication or snapshot retention. `requests` counts logical
store calls, not every HTTP request/retry; for example an S3 deletion internally
performs a HEAD. Read/write bytes are attempted client payload bytes.

Reads verify SHA-256 and length. The sequential packed test reads each pack once;
random/hot tests use exact byte ranges. “Hot” means repeated requests, not an
application cache. Host/storage caches are not forcibly dropped on a shared lab.
Trials rotate format order. Peak RSS is the process lifetime high-water mark,
not a clean per-format memory measurement.

Scattered retirement removes every tenth capture. Packed reclamation scans packs
and rewrites only those containing retired capture records or unreferenced bodies.
It publishes replacement commits before removing old commits/files, preserving
retirement tombstones with the complete original capture facts. This is offline single-writer reclamation: concurrent
readers and durable reader pins are not implemented here. Separate-layout sweep
is also a benchmark operation, not a claim that production already supports it.
Low-rate tests additionally retire a whole batch. Snapshot backup retention can
delay actual disk reclamation after S3 DELETE.

## Correctness proof

`test_layouts.py` covers all three layouts' exact round trips, shared-body
retirement, reclamation, index loss, lost acknowledgements after each packed
publication step, and process death followed by replay. Corrupt committed packs
must fail verification. These are bounded tests, not a durability SLA or proof
against concurrent writer/reader races.

`materialize.py` invokes the current `MaterialStore.materialize_many` with real
Postgres write claims and ClickHouse inserts/verification. Only its HTML byte
reader is adapted to the selected physical layout. It compares sorted output
digests and failures across formats and drops its UUID-named databases afterwards.
It calls the materializer one capture at a time for failure isolation. These
timings do not represent optimized 128-capture worker batching, NATS delivery,
live catch-up, atomic target publication or a full service-level rebuild.

## Reproduction

Use the project's locked Python environment from `packages/periplus`:

```sh
uv run python ../../benchmarks/archive/test_layouts.py
```

Inventory, fixture bodies and raw logs belong in ignored `.artifacts/archive-layout/`,
not Git. Run inventory export through the homelab operator wrapper:

```sh
uv run --locked python scripts/operator.py kubectl exec -i -n applications \
  deployment/periplus-query -- python - < /absolute/path/benchmarks/archive/lab_inventory.py \
  > /absolute/path/.artifacts/archive-layout/inventory.jsonl.gz
```

`lab-pod.yaml` is an explicitly temporary diagnostic, not Flux configuration. It
uses the existing raw bucket credentials, but every destination is validated as
`repository/benchmarks/archive-layout-<UUID>/...`. Source bodies are only read.
Copy the current `periplus` package, these scripts, and pinned pure-Python `warcio`
1.8.1 plus its `six` dependency into `/work`; set `PYTHONPATH=/work`. The old image
provides the native dependencies. Record the installed versions with the results.

```sh
python /work/archive/run.py --inventory /work/inventory.jsonl.gz \
  --cache /work/cache --output /work/results.jsonl --size 1000 --rounds 1
python /work/archive/arrival.py --inventory /work/inventory.jsonl.gz \
  --cache /work/cache --output /work/arrival.jsonl
python /work/archive/codec.py --inventory /work/inventory.jsonl.gz \
  --cache /work/cache --output /work/codec.jsonl --size 10000
python /work/archive/run.py --inventory /work/inventory.jsonl.gz \
  --cache /work/cache --output /work/repeated.jsonl --size 200 --workload repeated
python /work/archive/run.py --inventory /work/inventory.jsonl.gz \
  --cache /work/cache --output /work/concurrent.jsonl --size 200 --workload random --workers 4
```

Run storage experiments serially, not together. Ordinary application activity
remains a source of noise. The run logs its exact destination UUID before writing;
completed trials remove only their own target objects. On failure, inspect that
logged prefix and clean only that prefix. Never delete `repository/` or source
`html/` keys. Retrieve results before removing the diagnostic pod and policy.

For local materialization, use the existing Compose daemons and create isolated
databases; credentials are read from the ignored env file and never printed:

```sh
uv run python ../../benchmarks/archive/materialize.py \
  --inventory ../../.artifacts/archive-layout/inventory.jsonl.gz \
  --cache ../../.artifacts/archive-layout/cache \
  --output ../../.artifacts/archive-layout/material.json --env ../../.env
```

`lab-databases.yaml` records the attempted isolated homelab database setup. The
pinned amd64 ClickHouse image requires CPU features absent from the current VM
CPU model; it must not be mistaken for a successful lab deployment. Local
materializer proof and homelab storage timings must be reported separately.
