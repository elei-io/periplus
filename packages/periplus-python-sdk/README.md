# Periplus Python SDK

A read-only client for the public Periplus query API. Python 3.11 or later.
Configure the **public web application URL**, not the internal query or control service.
No API token, DuckDB installation or lake credentials are needed.

```python
from periplus_sdk import Client

with Client("http://localhost:8080") as client:
    result = client.execute(
        "SELECT capture_id FROM public_v1.capture LIMIT ?", [10]
    )
    print(result.columns, result.types)
    print(result.rows)
    print(result.source_snapshot, result.truncated)
```

For a hosted deployment, replace the URL with its public HTTPS origin. Alternatively set
`PERIPLUS_PUBLIC_URL` and use `Client()`. An optional URL path prefix is preserved.
The client reuses HTTP connections; close it with a context manager or `close()`.

## Marimo SQL cells and schema browser

Install the notebook integration from PyPI:

```sh
uv add "periplus-python-sdk[notebook]>=0.6.0"
```

In a Python setup cell, create a SQLAlchemy engine:

```python
from periplus_sdk import sql_api

pp = sql_api.create_engine("https://periplus.dev", mode="stable")
```

Add a SQL cell, select **pp** in its connection dropdown, and enter:

```sql
SELECT capture_id, requested_url
FROM public_v1.capture
LIMIT 10
```

Marimo displays the result as a table. Expand **pp → public_v1** in Data Sources
to discover views and expand a view to load its columns for SQL completion.
Discovery uses bounded `SHOW TABLES` and `DESCRIBE` through the same public API;
no internal catalogue or storage credentials are used. Truncated discovery fails
explicitly rather than displaying a silently incomplete schema. To eagerly load
schemas and views, enable their discovery in marimo's Packages & Data settings.
Column discovery is on demand by default, to avoid many public API requests.

The Python equivalent of a SQL cell is:

```python
import marimo as mo

captures = mo.sql(
    "SELECT capture_id FROM public_v1.capture LIMIT 10",
    engine=pp,
)
```

Set `mode="experimental"` for the experimental service. Omit the URL to use
`PERIPLUS_PUBLIC_URL`. Optional `timeout=140` and `schema_version="public_v1"`
arguments configure the client deadline and public schema. Run `pp.dispose()` when finished. This is a read-only
SQLAlchemy dialect for textual SQL and reflection, not a writable ORM backend.
Each statement has its own server snapshot; SQLAlchemy transaction blocks do not
provide a shared snapshot or rollback. The adapter makes no transaction requests.

A complete notebook is in `examples/notebook.py`. The integration is tested with
marimo 0.24.1 and SQLAlchemy 2.x. SQLAlchemy is included in the standard SDK install; the `notebook` extra adds
marimo. Existing marimo environments only need `uv add "periplus-python-sdk>=0.6.0"`.
The returned object is a standard SQLAlchemy Engine, also usable with pandas and
ordinary Python scripts. Engine creation is lazy; the first query opens a connection.

## DB-API connection

For SQL cells without schema browsing, or standard cursor-based Python code:

```python
from periplus_sdk import connect

with connect("https://periplus.dev", mode="stable") as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT capture_id FROM public_v1.capture LIMIT ?", [10])
        print(cursor.description)
        print(cursor.fetchall())
        print(cursor.result.source_snapshot)
```

Connections expose `cursor`, `execute`, `close`, and context managers. Cursors
support `execute`, `fetchone`, `fetchmany`, `fetchall`, iteration, and close.
Use positional `?` parameters. Decimal and temporal parameters are sent as
strings; use explicit SQL casts. Binary and nested parameters are not supported
by this adapter. Fetching only consumes the bounded result already received;
it never issues pagination or retries. Connections/cursors are not thread-shared.
`commit()` is a no-op; `rollback()` and `executemany()` are unsupported.

`cursor.result` preserves the original query response. `connection.last_result`
also retains it after marimo closes a cursor; a new execution clears it first.
Truncation emits `periplus_sdk.dbapi.TruncationWarning` and sets `rowcount` to -1.
DB-API failures use the standard exception hierarchy in `periplus_sdk.dbapi`;
HTTP errors retain `status_code`, `code`, and `retry_after_seconds`.

Scalar integer, floating-point, decimal, date, time, timestamp and BLOB results
are decoded to Python values. UUIDs remain strings. Nested/other SQL types keep
their JSON wire representation; out-of-range dates/timestamps remain strings.
Temporal precision is limited to what the server JSON transport preserves.
The cursor preserves duplicate column names, but dataframe libraries/marimo may
not: use unique SQL aliases. Dataframe inference can lose types for empty or
all-null results; `cursor.description` retains the SQL type names.

## Stable and experimental APIs

Both clients accept `mode="stable"` (the default) or `mode="experimental"` at initialization:

```python
with Client("https://periplus.dev", mode="experimental") as client:
    result = client.execute("SELECT capture_id FROM public_v1.capture LIMIT 1")
    print(result.query_mode, result.compiler_version, result.optimizations)
```

The selected mode applies to preparation, execution, and helper discovery. Experimental
requests use the public application's `/api/query/experimental/` routes. There is no
automatic fallback to stable if the experimental service is unavailable.
`AsyncClient` accepts the same option. Invalid modes raise `ConfigurationError`.

## Preparation and helpers

```python
with Client("http://localhost:8080") as client:
    prepared = client.prepare("SELECT capture_id FROM public_v1.capture LIMIT ?", [10])
    print(prepared.diagnostics, prepared.plan)
    result = client.execute(prepared.sql, prepared.parameters)
    helpers = client.helpers()
    print(helpers.catalogue_version, helpers.helpers)
```

Preparation validates and explains without executing the analytical query. Execution independently
validates and prepares; a prior preparation never authorizes SQL. Linting, diagnostics and future
SQL optimizations belong to the server. The SDK sends SQL unchanged.

## Async use

```python
from periplus_sdk import AsyncClient

async def observations():
    async with AsyncClient("http://localhost:8080") as client:
        return await client.execute("SELECT capture_id FROM public_v1.capture LIMIT 10")
```

Use `aclose()` when managing an async client's lifetime explicitly.

## Permissions, results and errors

- The same public SQL feature switch, shared rate budget, namespace validation and read-only
  execution apply as in the public web workspace. The SDK provides no writes, crawling,
  administrative controls or direct lake attachment.
- Results retain `query_id`, SQL, parameters, diagnostics, plan, columns, SQL types, JSON rows,
  elapsed milliseconds, `source_snapshot` and `truncated`. Decimals and large integers remain
  strings exactly as returned by the server. Duplicate column names are preserved.
- Operator-configured execution limits default to 1,000 rows, an 8 MiB result budget and a
  20-second server deadline. Always inspect `truncated`. The SDK does not silently fetch more rows or retry.
- `ApiError` exposes `status_code`, safe `code`, and `retry_after_seconds` when supplied.
  `TransportError` means HTTP failed; `ResponseError` means a malformed successful response.
  The client timeout defaults to 140 seconds and can be set with `timeout=`. A timeout or local
  cancellation does not guarantee server cancellation. Redirects are not followed automatically.
- Preparation and execution are attributed to `sdk` in the existing private query history.
  Original SQL and parameters are retained for 30 days; result rows are not stored. Recording is
  best-effort and can be lost during outages or backpressure. This label is not a user identity.

## Installation and verification

Install the public-v1 client from PyPI:

```sh
python -m pip install "periplus-python-sdk>=0.6.0"
```

Version 0.6.0 supports the current public-v1 contract. For production, configure
`PERIPLUS_PUBLIC_URL=https://periplus.dev`; no API token is required.
Run the installed package against an available public app:

```sh
PERIPLUS_PUBLIC_URL=http://localhost:8080 python packages/periplus-python-sdk/examples/smoke.py
```

## Releasing

Repository CI publishes immutable releases from tags named
`periplus-python-sdk-v<version>`. The tag must exactly match the static version
in `pyproject.toml`; for example, version `0.6.0` is released with:

```sh
git tag periplus-python-sdk-v0.6.0
git push origin periplus-python-sdk-v0.6.0
```

PyPI publishing uses Trusted Publishing rather than a stored API token. The
PyPI publisher must be configured for GitHub owner `elei-io`, repository
`periplus`, workflow `python-sdk-release.yml`, and environment `pypi`. Protect
that GitHub environment with required reviewers before the first release.

## Public v1

Install the updated SDK from PyPI with `python -m pip install "periplus-python-sdk>=0.6.0"`. The previously published 0.2.0 release predates this contract. `prepare` and `execute` accept keyword-only `schema_version="public_v1"` (the default); responses preserve `schema_version` separately from `source_snapshot`. Unavailable versions are rejected by the server.

## License

Copyright (c) 2026 Ekku Leivonen (elei.io). Licensed under [Apache-2.0](LICENSE);
see [NOTICE](NOTICE). The server and other repository packages have separate
licensing described in the root LICENSING.md.
