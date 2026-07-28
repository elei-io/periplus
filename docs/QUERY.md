# Query

Atlas delivers its query interface in three layers, in this order.

## 1. Portable DuckLake catalogue

Atlas initialization installs and versions the `web.*` interface as persistent DuckLake views,
scalar macros, and table macros over `ingest.*` and `material.*`. These catalogue objects define
the complete semantics of `web.*` and must remain correct without an Atlas SDK or native extension.
They are changed only by Atlas catalogue upgrades, never by Quack replica startup.

Recurring expensive computations belong in Atlas-owned `material.*` relations maintained through
the ordinary materialization lifecycle. Quack is an optional execution transport, not a dependency
of the catalogue contract.

## 2. Python SDK

`packages/atlas-python-sdk/` is the first client package. It wraps DuckDB connection setup and
results, Atlas authentication, and control-plane operations such as crawl submission and run
tracking. It does not define `web.*` semantics or compile and rewrite user SQL.

## 3. Optional native optimization

Only measured query-plan gaps justify native code. If required, Atlas will ship one exact-version
C++ DuckDB extension containing SQL rewrite rules and specialist functions. The extension may make
portable `web.*` queries faster but must never be required for correctness.

Atlas will not maintain parallel stable-C and C++ production extensions or CI paths. With Quack,
the matching extension is loaded on the server and the complete query executes there. Without
Quack, a user may load the matching extension locally. In both cases the DuckLake catalogue remains
the authoritative interface. The local build, direct DuckLake, and differential testing workflow is
documented in [`EXTENSION_DEVELOPMENT.md`](EXTENSION_DEVELOPMENT.md).
