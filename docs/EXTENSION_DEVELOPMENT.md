# DuckDB extension development

Atlas keeps portable query semantics in the persistent `web.*` and `dom.*` DuckLake catalogue.
The optional C++ extension recognizes and accelerates those plans; it must not be required for
correctness.
See [`QUERY.md`](QUERY.md) for the query boundary.

This guide describes the local development loop. It deliberately connects directly to Basin's
underlying DuckLake so an extension can be rebuilt and exercised without first installing it on a
Quack server. Production Atlas still uses Quack and never receives lake metadata or object-store
credentials.

## Layout and prerequisites

The repositories are siblings:

```text
Code/
├── atlas/
│   ├── .env.extra
│   ├── ducklake.sh
│   └── backend/scripts/direct_ducklake.py
└── atlas-duckdb-extension/
    ├── duckdb/
    ├── src/atlas_extension.cpp
    └── test/sql/
```

The extension repository comes from DuckDB's official C++ extension template. Its DuckDB
submodule and Atlas's Python `duckdb` dependency must remain on the exact same version because
loadable C++ extensions are version- and platform-specific.

The root `.env.extra` contains the development-only Basin metadata and object-store connection
details. It is ignored by Git and must never be committed, copied into Atlas runtime
configuration, or used as a production fallback.

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

The shell defaults to the `atlas_test` lake and opens DuckDB's native interactive terminal. Useful
terminal commands include `.tables`, `.schema`, `.help`, and `.quit`.

To execute one statement without entering the terminal:

```sh
./ducklake.sh --sql \
  "SELECT web._catalogue_version(), count(*) FROM web.pages"
```

All arguments accepted by `backend/scripts/direct_ducklake.py` pass through the wrapper. For
example:

```sh
./ducklake.sh \
  --lake atlas_test \
  --sql "EXPLAIN SELECT * FROM web.pages LIMIT 10"
```

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

The Python client:

- resolves the requested lake from Basin metadata using a read-only PostgreSQL session;
- creates DuckDB secrets for the metadata store and lake object storage;
- attaches the DuckLake with `READ_ONLY`;
- loads the local release extension for one-shot Python queries; and
- starts the extension-enabled native DuckDB terminal when `--sql` is omitted.

For an interactive session, credentials exist only in a mode-`0600` temporary initialization file.
The file is held inside a private temporary directory and removed when the terminal exits.

## Optimizer testing requirements

Before implementing an optimizer rule, record the performance-triage classification required by
[`QUERY.md`](QUERY.md). Native work is appropriate only for the compiler part of the issue after
any required schema or catalogue correction. A warning or boundedness error may be the correct
compiler action when execution should not be rewritten.

Every optimizer rewrite needs evidence for both semantics and activation:

- A differential correctness test must compare the portable and optimized result bags.
- An `EXPLAIN` assertion or another positive signal must prove the intended rule fired.
- Tests must cover empty and `NULL` inputs, duplicate preservation, relevant limit/order
  behavior, and aliases or projections affected by the rewrite.
- The same public query must remain correct when the Atlas extension is absent.

The selector optimizer preserves keyed DuckLake pruning by increasing DuckDB's dynamic hash-join
`IN` filter threshold only for plans that contain Atlas DOM selector functions. Explicit user
settings take precedence. Native selectors consume the one-row value returned by
`dom.document(content_id)` so plans never need to aggregate the complete element relation into
scope-wide nested lists.

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

## Local versus managed execution

This loop validates the extension against the real lake without involving Quack. It does not prove
managed deployment compatibility.

For managed execution, Basin must install the exact matching extension on the Quack server and
load it into the server-side DuckDB process. The client-side extension is not shipped through a
Quack connection, and installing it only on a Quack client cannot optimize a query executed by the
server.

Before publishing or requesting a Basin installation:

1. run the debug and release SQLLogicTests;
2. verify the release artifact against `atlas_test`;
3. confirm the DuckDB ABI/version matches the managed Quack runtime; and
4. repeat the representative query and plan checks through a Quack server with the extension
   loaded server-side.
