# Compact JSON-LD storage experiment

Classification: both. The JSON-LD view repeatedly reconstructs a small structured
relation from general HTML elements; selected-key propagation is a separate
remaining optimizer/access-path question. This experiment isolates the former.

Hypothesis: storing complete JSON-LD script records once substantially reduces
physical work for latest-capture bulk extraction. Eight content-ID buckets and
sorting by `(content_id, node_index)` may additionally improve selective lookup.

Use an isolated temporary DuckLake on pod-local storage populated from one frozen
production read snapshot. Both baseline and candidate use that same storage and
engine. Baseline retains all HTML element rows but only the six columns required
by this workload; this favors the baseline versus copying unused structural fields.
Compare the current view to compact unsorted/unpartitioned, compact sorted, and
compact eight-bucket sorted tables. Keep public JSON values and errors unchanged.
Use shared benchmark measurement/digest/profile helpers, run both orders, and
record build time and bytes separately. Local storage timings are not S3/API
latencies. This does not establish one-billion-content scaling or incremental
rebuild/live parity; no production catalogue change is part of the experiment.

## Isolated results, 2026-09-10

Source snapshot **99174**, production image **06e406c**, DuckDB 1.5.5.
All 49,734 JSON-LD records match the baseline using bidirectional `EXCEPT ALL`.
All query comparisons match complete typed result bags in both execution orders.

| Workload | Baseline warm | Plain compact | Sorted compact | Eight-bucket sorted |
| --- | ---: | ---: | ---: | ---: |
| Tori, 500 results | 2.58–2.62 s | 101–121 ms | 100–105 ms | 59 ms |
| Tori, all 6,202 products | 2.61–2.64 s | 100–101 ms | 115–122 ms | 65–73 ms |
| One content, three scripts | 14–16 ms | 34–38 ms | 34–36 ms | 9 ms |

Registered Parquet storage: baseline six-column HTML relation **1,918,826,116
bytes / eight files**; compact bucket-sorted relation **34,232,223 bytes / eight
files**. Plain/sorted compact alternatives each occupy about 34.2 MB in one file.
These are stored file sizes, not per-query bytes. Warm profiles report zero read
bytes after caching, so they cannot establish reduced network traffic.

The baseline was exported as 767.8 MB ZSTD Parquet then loaded into the isolated
DuckLake. Large sorted writes failed under 512 MB and 1 GB build budgets; bounded
content-prefix batches completed with 1 GB and 4 GB spill. DuckLake checkpoint
compaction left eight active baseline files; discarded build files are not counted
in the registered size above. Measurements used identical 512 MB memory, two
threads, 256 MB spill and native optimizer settings. Each compact build took about
4.4 seconds from the isolated baseline; this excludes production HTML parsing,
materializer scheduling and object-store writes.

The compact representation provides the bulk of the gain; sorting alone did not
improve this small one-file compact corpus. Eight buckets improve these measured
cases, but are not a demonstrated billion-content solution. Production rebuilds
initially create more small files than this compacted experiment. Actual S3/API
latencies and maintenance behavior require post-activation verification.

## Production implementation and acceptance

One new auto-discovered `html_jsonld.py` projection owns schema, partitioning,
sort policy and parsing. It uses the shared parsed elements and bounded DuckDB
syntax parsing. The public view reads that projection. The existing generation
lifecycle must rebuild/catch up and atomically activate; setup preserves the old
public view while registry digests differ. No custom optimizer is activated for
this workload. The research scoper treats JSON-LD as a primitive public relation
so it does not expand the new view into private storage SQL.

Require projector/parser differential fixtures, generic materialization Parquet
registration tests, full repository checks and live same-snapshot equivalence
against `baseline.sql` before claiming production acceptance. The user explicitly
authorized production materialization/table creation and removal if unsuccessful.

`layout.sql` records the exact three candidate layouts. For paired query replay,
replace only `public_v1.html_jsonld` in the registered `tori-latest-products` case
with each candidate table and use the shared bench against the disposable lake.
Repeat both orders, then remove the final `LIMIT 500` for full-domain extraction.
The single-content case is `SELECT * FROM public_v1.html_jsonld WHERE content_id=?`
with one fixed known nonempty content key for every variant. Do not compare a
local candidate against a remote baseline or include projection build time in only
one side of a query-latency comparison.

DuckLake references: [partitioning](https://ducklake.select/docs/stable/duckdb/advanced_features/partitioning)
and [sorted tables](https://ducklake.select/docs/stable/duckdb/advanced_features/sorted_tables).
Periplus declares both policies; LakeDucktor owns production physical maintenance.

### Deployment guard discovered before rollout

Review after PR #41 found that `_active_registry_matches` accepted absent tables,
so setup would create an empty new projection and publish its view before rebuild.
The rollout was held. Require every projection in an existing active schema, defer
all active material-table changes during a mismatch, and create the new relation's
empty retirement marker only inside the atomic activation transaction. This uses
the existing replay protocol. A real DuckLake regression test checks repeated
setup, old-view availability, rollback, successful activation and replay after a
lost control acknowledgement. Deploy the guard with the JSON-LD change.
