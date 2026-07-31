# DuckDB extension development

Atlas keeps portable query semantics in the persistent `web.*` and `dom.*` DuckLake catalogue.
The C++ extension supplies bounded selector operations and recognizes and accelerates eligible
plans; portable base catalogue semantics do not require it.
See [`QUERY.md`](QUERY.md) for the query boundary.

This guide describes the local development loop against Atlas's own DuckLake attachment.

## Layout and prerequisites

The repositories are siblings:

```text
Code/
├── atlas/
│   ├── .env
│   ├── ducklake.sh
│   └── backend/scripts/direct_ducklake.py
├── atlas-duckdb-extension/
    ├── duckdb/
    ├── src/atlas_extension.cpp
    └── test/sql/
└── quack/
    └── ducklake-cdc-extension-1.5.5/
```

The extension repository comes from DuckDB's official C++ extension template. Its DuckDB
submodule and Atlas's Python `duckdb` dependency must remain on the exact same version because
loadable C++ extensions are version- and platform-specific.

Compose passes both extension sources as additional build contexts. Cached Linux builder stages
compile both against DuckDB 1.5.5, and the final Atlas image contains only
`/opt/atlas/atlas.duckdb_extension` and
`/opt/atlas/ducklake_cdc.duckdb_extension`. `atlas-setup` installs the persistent catalogue and
bootstraps CDC state. Ordinary catalogue connections load only the Atlas extension; the dedicated
live-materialization connection loads and prewarms the CDC extension before attaching DuckLake.

The shell uses the same centralized DuckLake connection factory contract as Atlas:
`ATLAS_DUCKLAKE_ALIAS`, `ATLAS_DUCKLAKE_METADATA_PATH`,
`ATLAS_DUCKLAKE_METADATA_SCHEMA`, and `ATLAS_DUCKLAKE_DATA_PATH`, plus protocol-specific
credentials such as `ATLAS_DUCKLAKE_S3_*`. The configured data path and endpoint must be reachable
by the host-native DuckDB process.

## First build

Build and test both native configurations:

```sh
cd ../atlas-duckdb-extension
make debug
make test_debug
make release
make test_release
```

The first build compiles DuckDB and takes longer. Subsequent extension-only rebuilds are
incremental.

On macOS, the debug build is sanitizer-instrumented. Use it with the template's native test
runner. The stock DuckDB Python wheel cannot safely load that debug artifact, so the direct Atlas
connection uses the release build.

## Daily development loop

1. Change the optimizer or specialist functions under
   `atlas-duckdb-extension/src/`.
2. Add or update SQLLogicTests under `atlas-duckdb-extension/test/sql/`.
3. Run the debug tests:

   ```sh
   cd ../atlas-duckdb-extension
   make debug
   make test_debug
   ```

4. Build and test the loadable release artifact:

   ```sh
   make release
   make test_release
   ```

5. Exercise it against the development DuckLake:

   ```sh
   cd ../atlas
   ./ducklake.sh
   ```

The shell opens the configured lake in DuckDB's native interactive terminal. Useful
terminal commands include `.tables`, `.schema`, `.help`, and `.quit`.

To execute one statement without entering the terminal:

```sh
./ducklake.sh --sql \
  "SELECT web._catalogue_version(), count(*) FROM web.page"
```

All arguments accepted by `backend/scripts/direct_ducklake.py` pass through the wrapper.

The `web.*` and `dom.*` objects are persistent DuckLake catalogue definitions installed by
`make setup` or,
when only the analytical catalogue needs reconciliation:

```sh
cd backend
uv run python -m atlas.platform.catalogue bootstrap
```

The direct client attaches the lake read-only. It can verify and exercise both public namespaces,
but cannot
install or replace catalogue definitions.

If the extension repository is not the default sibling, point the wrapper at it:

```sh
ATLAS_DUCKDB_EXTENSION_REPO=/absolute/path/to/atlas-duckdb-extension \
  ./ducklake.sh
```

## What the wrapper does

`ducklake.sh` selects the matching release extension and native DuckDB CLI, then delegates
connection setup to `backend/scripts/direct_ducklake.py`.

The interactive CLI has the Atlas extension linked into its executable, so run `make release` and
restart the terminal after every extension change. One-shot `--sql` execution loads the matching
release extension into the pinned Python DuckDB process.

The Python launcher loads the release extension, attaches the configured DuckLake read-only, and
starts the extension-enabled native DuckDB terminal.

For an interactive session, credentials exist only in a mode-`0600` temporary initialization file.
The file is held inside a private temporary directory and removed when the terminal exits.

## Optimizer testing requirements

Before implementing an optimizer rule, record the performance-triage classification required by
[`QUERY.md`](QUERY.md). Native work is appropriate only for the compiler part of the issue after
any required schema or catalogue correction. A warning or boundedness error may be the correct
compiler action when execution should not be rewritten.

Every optimizer rewrite or native public capability needs evidence for both semantics and
activation:

- A differential correctness test must compare the portable and optimized result bags.
- An `EXPLAIN` assertion or another positive signal must prove the intended rule fired.
- Tests must cover empty and `NULL` inputs, duplicate preservation, relevant limit/order
  behavior, and aliases or projections affected by the rewrite.
- Base portable queries must remain correct when the Atlas extension is absent. An explicitly
  extension-backed capability must instead be absent from installation and metadata.

Native selectors are table-in/table-out operators. Public selector macros supply a keyed
`dom.elements` slice followed by a typed end-of-document sentinel. The native operator buffers and
reconstructs only that document, matches with Lexbor, and returns complete element rows. Tests must
prove that content predicates prune before the operator, input spanning multiple DuckDB vectors is
not truncated, and lateral calls do not combine documents.

Optimizer actions and diagnostics share `AtlasPlanAnalyzer` and the ordered Atlas policy registry.
A new rule must be based on reusable plan facts such as relation grain, capability, cardinality,
expansion, or blocking state. Do not add a second whole-plan visitor for one SQL spelling or
function. Hard errors must follow the relevant dataflow and have high-confidence activation tests;
warnings may be advisory but need a specific rewrite hint.

Add SQLLogicTests for:

- the triggering plan and diagnostic code;
- a bounded or otherwise safe neighboring plan;
- an unrelated Atlas query family to guard against overmatching;
- independent plan branches that must not be treated as one dataflow; and
- plain `EXPLAIN` access plus enforced `EXPLAIN ANALYZE` for any plan rejected during normal
  execution.

`atlas_lint_query(sql, profile := 'interactive')` binds and optimizes one `SELECT` or `EXPLAIN`
without executing it. Use it to assert structured warnings and would-be errors. Keep lint output
stable and machine-readable; shell and SDK presentation belongs outside the extension.

Use real `atlas_test` queries for plan and performance investigation, but keep deterministic
correctness coverage in SQLLogicTests.

## Release validation

Before publishing an extension build:

1. run the debug and release SQLLogicTests;
2. verify the release artifact against `atlas_test`;
3. confirm the DuckDB ABI/version matches Atlas's pinned DuckDB version; and
4. repeat the representative query and plan checks against the configured DuckLake.
