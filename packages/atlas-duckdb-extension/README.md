# Atlas DuckDB extension

This package is the native development boundary for moving Atlas catalogue
compilation into DuckDB. It is based on DuckDB's experimental
[`extension-template-c`](https://github.com/duckdb/extension-template-c) and
uses the stable DuckDB C extension API by default.

The extension is deliberately only a loadable smoke test today. It is not
part of setup, runtime, readiness, or the active test suite. The former Python
compiler is inert reference material under `archive/python_compiler/`; the
future C compiler must target the established `ingest.*` and `material.*`
contracts directly.

## Requirements

- A C11 compiler
- CMake
- Make
- Python 3.14 with `venv`
- Git submodules initialized recursively
- Optional: Ninja and ccache for faster builds

From the Atlas repository root:

```sh
git submodule update --init --recursive
make configure
make debug
make test_debug
```

The equivalent package-local workflow is:

```sh
cd packages/atlas-duckdb-extension
make configure
make debug
make test_debug
```

The loadable debug artifact is written to:

```text
build/debug/atlas.duckdb_extension
```

Release builds use the package-local `make release`.

## DuckDB compatibility probes

The default `TARGET_DUCKDB_VERSION` is `v1.2.0`, the stable C-extension ABI
baseline accepted by current DuckDB runtimes. It is intentionally different
from `DUCKDB_TEST_VERSION`, which defaults to DuckDB 1.5.4. Test the same
stable-ABI binary against a prerelease client with:

```sh
cd packages/atlas-duckdb-extension
make clean_all
DUCKDB_TEST_VERSION=main make configure
make debug
make test_debug
```

When a required DuckDB 2.0 capability exists only in the unstable API, isolate
the experiment and opt in with `USE_UNSTABLE_C_API=1`, an exact DuckDB
version, matching headers, and the matching test runtime. Do not use `main` as
the unstable target: it does not identify an exact ABI. The current upstream
build helper also recognizes only the v1 stable-version shape, so a DuckDB 2.0
unstable probe must wait for corresponding template support or use a
short-lived, explicitly version-pinned harness.

Never enable the unstable API for release builds. Its binaries are tied to one
exact DuckDB build.

## Deployment shape

The package is self-contained so it can be subtree-published as the root of a
small extension repository when Atlas is ready to distribute it. DuckDB's
Community Extensions builder currently expects the checked-out extension at
the repository root and does not accept a monorepo source-directory setting.

Before publishing, the release repository will add DuckDB's distribution
workflow and a Community Extensions `description.yml`. Until the C template's
Community Extensions support is finalized, Atlas CI builds and loads the
extension directly from this package.
