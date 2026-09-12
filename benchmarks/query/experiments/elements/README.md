# Canonical element integration

This is schema/catalogue work. Text lives directly on every element; no search
rewrite or posting index participates. Use the actual projection registry,
prepare_batch, commit_prepared_batch, Parquet writer and shared query benchmark.
The fixture injects retained HTML source selection and uses isolated SQLite control
state. It does not simulate NATS, production Postgres latency, object-store latency,
multiple writer pods or the entire production corpus.

Run from the repository with the normal backend environment:

```sh
uv run --project packages/periplus python benchmarks/query/experiments/elements/production_shape.py \
  --fixture /path/to/retained-fixtures --output /tmp/elements-integration
```

The fixture contains manifest.json entries with id (SHA-256), file and bytes,
and zstd-compressed raw HTML under objects/. Output must be a new directory.
500 retained documents are shuffled and published in batches of 50 with two parser
processes. Publication replay must not duplicate rows. Scalar projection checks
must pass. Queries record latency, result digests and files read using the shared bench.

Compaction must use the deployment's pinned LakeDucktor image. Stock DuckLake
revision d8a1881e rejects external per-batch Hive paths; production already includes
the external-Hive and sorted-compaction patches. Mount the disposable lake at the
same absolute path used when building it and run compact.py via the image's Python
entrypoint. On macOS also mount the /private/tmp alias if metadata references it.
Never point this harness at production storage.

See results.md for the tested revision, resource budgets and measurements.
