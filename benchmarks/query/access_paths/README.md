# Native ClickHouse access-path experiments

Read [REPORT.md](REPORT.md) for the measured results and their limits. This is a
private benchmark under the shared query bench, not a replacement runtime schema.
It compares document arrays, actual element rows, native text indexes, Map
indexes and projections against the same retained captures.

The experiment intentionally targets the pre-refactor document-array recipe.
Its source loaders are historical reproduction tools, not adapters for the new
flat runtime. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for runtime validation
and rollout requirements.

## Boundaries

- Source: one explicitly named immutable `material_<32 hex>` build. Never use a
  moving public alias as the input snapshot.
- Destination: a new `bench_access_*` database, temporary native workload and
  disposable probe pod. No source updates, raw-object writes, public grants,
  publication changes or API query rewrites.
- Queries: two ClickHouse threads, 512 MiB memory, 1 GiB scanned bytes, 20 seconds,
  1,001 result rows. Query/result-condition caches disabled. First and repeated
  runs are recorded; first does not mean cold.
- Cache-bypass diagnostics disable this query's filesystem and text-index
  caches. They do not flush shared caches or certify cold NAS/HDD access.
- Writes/verification: separate diagnostic limits, generally 2 GiB. A failed
  INSERT may have committed parts. Writers stop and never blindly retry.
- Generated evidence stays in ignored `.artifacts/access-paths/`. Reports contain
  aggregate measurements, not retained page payloads or credentials.

## Reproduce

Requires the managed homelab operator, a compatible ClickHouse installation,
an existing Periplus ingestor deployment/image, and access to the pinned source.
The probe borrows existing secret references; credentials are not written here.

Run from the repository root. Choose a fresh database name and a source build
that still exists. Preserve any previous `.artifacts/access-paths/` directory
before a new campaign; result files append within one campaign.

```sh
export PERIPLUS_BENCH_DATABASE=bench_access_rerun_20260917
export PERIPLUS_BENCH_SOURCE=material_2a70e43637aa42a5932e3caa2654d889
export PERIPLUS_BENCH_HOMELAB=/Users/ekku/Code/homelab
python3 benchmarks/query/access_paths/setup.py init
python3 benchmarks/query/access_paths/probe.py create
python3 benchmarks/query/access_paths/campaign.py
python3 benchmarks/query/access_paths/diagnostics.py
python3 benchmarks/query/access_paths/attribute_cases.py
python3 benchmarks/query/access_paths/fanout.py
python3 benchmarks/query/access_paths/vocabularies.py
python3 benchmarks/query/access_paths/json_keywords.py
python3 benchmarks/query/access_paths/document_text.py
python3 benchmarks/query/access_paths/verify_results.py
python3 benchmarks/query/access_paths/storage.py
python3 benchmarks/query/access_paths/experiment.py metrics
```

The example source must still exist and the destination must be unused. The sample is intentionally
pinned to positive IC3 and Product examples in the retained fixture. A different
archive needs new positive anchors and a reviewed oracle, not empty-result
substitutions. `setup.py resume` only resumes missing whole source identities;
candidate range loads reject nonempty ranges. Partial flat-row writes require
inspection and a fresh disposable table before replay. The runner is an
experiment, not production retry machinery.

`campaign.py` makes cumulative 200, 2,000 and 20,000-document comparisons. The
historical run developed follow-ups during execution, so its exact order differs
from this consolidated recipe; every executed SQL statement and query ID is in
`operations.jsonl`. The failed all-in-SQL full-text expansion remains reproducible
through `load.element_insert(..., full=True)` on a **fresh** disposable table. The
normal campaign uses the successful bounded RowBinary writer instead.

`probe.py worker` reads already parsed documents; it isolates encoding and writes.
`probe.py parser` fetches actual retained bodies and checks canonical text/node
counts. `probe.py raw` fetches, verifies, parses and writes 100 actual bodies to
`elements_raw_probe`. None exercises production queues, receipts or activation.

`fanout.py` stops merges only for its private diagnostic tables. It spreads the
same real document keys across 2, 20 and 100 overlapping parts, then compares a
fixed 64-bucket hash partition. It does not fabricate additional web content.

After collecting evidence, remove the probe first, check that no campaign query
is still running, then delete the private database and workload:

```sh
python3 benchmarks/query/access_paths/probe.py delete
python3 benchmarks/query/access_paths/setup.py cleanup
```

## Evidence and checks

`operations.jsonl` holds SQL, query IDs, responses and failures; `results.jsonl`
holds case/scale/run labels and answer hashes. `metrics.json` joins exact query
IDs to ClickHouse's final query-log records, including physical reads, CPU,
memory, object operations and projection use. `physical-storage.json` records
active bytes, index/projection components and completed merge work. Bytes in
projection/index breakdowns are components, not additions to total table bytes.
`correctness.json` records source coverage, independent positive oracles,
cross-layout comparisons and node-identity completeness checks. `worker-*.jsonl`,
`parser-probe.jsonl` and `raw-probe.jsonl` distinguish write-only and raw parsing
costs. `vocabularies.jsonl` measures distinct units separately from occurrences.

```sh
uv run --project packages/periplus python -m unittest discover \
  -s benchmarks/query/access_paths -p 'test_*.py'
uvx ruff check benchmarks/query/access_paths
```

The wire tests cover Unicode separators and code-point text spans; the transport
test verifies that an uncertain write is recorded with its identity and not
replayed. Result normalization permits unambiguous column qualification only;
it preserves values, duplicates and order. Unordered previews are checked
against their source rows rather than compared as if row order were guaranteed.
