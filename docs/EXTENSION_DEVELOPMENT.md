# DuckDB extension development

Periplus keeps all public query semantics in the four persistent DuckLake views
defined by [`SCHEMA.md`](SCHEMA.md). The C++ extension contributes only query
safety and measured, semantics-preserving optimizer rules. Public catalogue
queries must remain correct when the extension is absent.

## Repositories and versioning

The Periplus and extension repositories are siblings:

```text
Code/
├── periplus/
└── periplus-duckdb-extension/
    ├── duckdb/
    ├── src/
    └── test/sql/
```

The extension uses DuckDB's official C++ extension template. Its DuckDB
submodule must stay pinned to the exact DuckDB version used by Periplus because
loadable extensions are version- and platform-specific.

Periplus images compile the matching extension and make it available to hosted
query connections. Ordinary clients may attach Periplus DuckLake without it. The
dedicated live-materialization connection separately loads the DuckLake CDC
extension; these two extensions have no shared query contract.

## Development loop

Build and test both configurations:

```sh
cd ../periplus-duckdb-extension
make debug
make test_debug
make release
make test_release
```

The first build compiles DuckDB. Later extension-only builds are incremental.
The macOS debug build is sanitizer-instrumented and should be exercised through
the template's native runner. Use the release artifact with the stock DuckDB
Python wheel.

Exercise the release build against the configured development lake:

```sh
cd ../periplus
./ducklake.sh --sql \
  "SELECT count(*) FROM web.observation"
```

`ducklake.sh` uses the centralized `PERIPLUS_DUCKLAKE_*` attachment contract. If
the extension repository is not the default sibling, set
`PERIPLUS_DUCKDB_EXTENSION_REPO` to its absolute path.

## Optimizer requirements

Before adding a rule, classify the issue as schema/catalogue design,
compiler/optimizer behavior, or both using [`QUERY.md`](QUERY.md). Native work
is justified only for the compiler portion after correcting any poor public
schema.

Every rewrite requires:

- a differential test comparing portable and optimized result bags;
- an `EXPLAIN` assertion or another positive proof that the rule fired;
- neighboring cases where the rule must not fire;
- coverage for NULLs, empty inputs, duplicates, and relevant projections or
  filters; and
- confirmation that the public query remains correct with extension
  optimizers disabled.

The first optimizer removes `web.observation`'s visit/document left join when
no document-derived value is used. It relies on Periplus's one-document-per-visit
invariant. Selecting, filtering, grouping, or ordering by `content_id` must
retain the join and its NULL semantics.

## Safety requirements

Optimizer actions and diagnostics share one `PeriplusPlanAnalyzer` and policy
evaluation. Rules should consume reusable facts such as physical Periplus grain,
cardinality, expansion, or blocking state; do not add a whole-plan visitor for
one SQL spelling.

For a safety rule, test:

- its triggering plan and stable diagnostic code;
- a bounded or otherwise safe neighboring plan;
- an unrelated Periplus query family;
- an intentional batch-profile case; and
- plain `EXPLAIN` plus enforced execution behavior.

`periplus_lint_query(sql, profile := 'interactive')` binds and optimizes one
`SELECT` or `EXPLAIN` without executing it. Keep its diagnostics stable and
machine-readable. Hosted presentation belongs outside the extension.

## Release validation

Before publishing:

1. run debug and release SQLLogicTests;
2. verify the release artifact against the development DuckLake;
3. confirm the DuckDB ABI/version matches Periplus; and
4. repeat representative plan and performance checks on the configured lake.
