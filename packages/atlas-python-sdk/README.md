# Atlas Python SDK

The first public SDK surface is intentionally small:

```python
import atlas_sdk

atlas_sdk.configure(api_url="http://localhost:8000")

connection = atlas_sdk.conn.duck()

crawl = await atlas_sdk.crawls.run(
    ["https://example.com/", "https://example.org/"],
    depth=0,
    max_crawls=2,
)
await crawl.completed()
crawl.raise_for_status()
```

The PyPI distribution is named `atlas-python-sdk`; its Python import package
is `atlas_sdk`. This keeps it safe to install alongside the Atlas backend,
whose import package is `atlas`.

`duck()` loads the exact-version Atlas extension, then returns an ordinary
`duckdb.DuckDBPyConnection` attached read-only to the configured Atlas DuckLake.
Set `ATLAS_DUCKDB_EXTENSION_PATH` to the platform artifact produced by the Atlas extension CI.
The connection fails clearly when the artifact is missing or incompatible.

Lifecycle waits are local, cancellable polling operations. Timing out or
cancelling a wait never cancels the server-side crawl; call `crawl.cancel()`
explicitly for that transition. Complete catalogue rebuilds are started and
observed independently through Atlas operations.

Install the released package from PyPI:

```sh
python -m pip install atlas-python-sdk
```

For local development, install directly from the repository:

```sh
python -m pip install ./packages/atlas-python-sdk
```

The standalone end-to-end proof is `examples/smoke.py`. It uses
`ATLAS_API_URL`, `ATLAS_DUCKDB_EXTENSION_PATH`, and the `ATLAS_DUCKLAKE_*`
connection variables, or `ATLAS_DIRECT_ENV_FILE`.

`atlas_sdk.conn.DuckLakeConnectionFactory` selects filesystem and S3 protocols centrally.
`atlas_sdk.conn.duck()` accepts an injected `DuckLakeConnectionProtocol` for another
DuckDB-supported data URI. S3-compatible endpoints use the
`ATLAS_DUCKLAKE_S3_ENDPOINT`, `ATLAS_DUCKLAKE_S3_REGION`,
`ATLAS_DUCKLAKE_S3_KEY_ID`, `ATLAS_DUCKLAKE_S3_SECRET_ACCESS_KEY`,
`ATLAS_DUCKLAKE_S3_SESSION_TOKEN`, `ATLAS_DUCKLAKE_S3_URL_STYLE`, and
`ATLAS_DUCKLAKE_S3_USE_SSL` settings; standard AWS credential variables are accepted when the
Atlas-specific credential variables are absent.

## Releasing

Repository CI publishes immutable releases from tags named
`atlas-python-sdk-v<version>`. The tag must exactly match the static version
in `pyproject.toml`; for example, version `0.1.0` is released with:

```sh
git tag atlas-python-sdk-v0.1.0
git push origin atlas-python-sdk-v0.1.0
```

PyPI publishing uses Trusted Publishing rather than a stored API token. The
PyPI publisher must be configured for GitHub owner `ekkuleivonen`, repository
`atlas`, workflow `python-sdk-release.yml`, and environment `pypi`. Protect
that GitHub environment with required reviewers before the first release.
