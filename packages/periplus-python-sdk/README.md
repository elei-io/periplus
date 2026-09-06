# Periplus Python SDK

The first public SDK surface is intentionally small:

```python
import periplus_sdk

periplus_sdk.configure(api_url="http://localhost:8000")

connection = periplus_sdk.conn.duck()

crawl = await periplus_sdk.crawls.run(
    ["https://example.com/", "https://example.org/"],
    depth=0,
    max_crawls=2,
)
await crawl.completed()
crawl.raise_for_status()
```

The PyPI distribution is named `periplus-python-sdk`; its Python import package
is `periplus_sdk`. This keeps it safe to install alongside the Periplus backend,
whose import package is `periplus`.

`duck()` returns an ordinary `duckdb.DuckDBPyConnection` attached read-only to
the configured Periplus DuckLake. It uses standard DuckDB and official storage extensions. Query policy and optimizations belong to the hosted query API.

Lifecycle waits are local, cancellable polling operations. Timing out or
cancelling a wait never cancels the server-side crawl; call `crawl.cancel()`
explicitly for that transition. Complete catalogue rebuilds are started and
observed independently through Periplus operations.

Install the released package from PyPI:

```sh
python -m pip install periplus-python-sdk
```

For local development, install directly from the repository:

```sh
python -m pip install ./packages/periplus-python-sdk
```

The standalone end-to-end proof is `examples/smoke.py`. It uses
`PERIPLUS_API_URL` and the `PERIPLUS_DUCKLAKE_*`
connection variables, or `PERIPLUS_DIRECT_ENV_FILE`.

`periplus_sdk.conn.DuckLakeConnectionFactory` selects filesystem and S3 protocols centrally.
`periplus_sdk.conn.duck()` accepts an injected `DuckLakeConnectionProtocol` for another
DuckDB-supported data URI. S3-compatible endpoints use the
`PERIPLUS_DUCKLAKE_S3_ENDPOINT`, `PERIPLUS_DUCKLAKE_S3_REGION`,
`PERIPLUS_DUCKLAKE_S3_KEY_ID`, `PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY`,
`PERIPLUS_DUCKLAKE_S3_SESSION_TOKEN`, `PERIPLUS_DUCKLAKE_S3_URL_STYLE`, and
`PERIPLUS_DUCKLAKE_S3_USE_SSL` settings; standard AWS credential variables are accepted when the
Periplus-specific credential variables are absent.

## Releasing

Repository CI publishes immutable releases from tags named
`periplus-python-sdk-v<version>`. The tag must exactly match the static version
in `pyproject.toml`; for example, version `0.1.0` is released with:

```sh
git tag periplus-python-sdk-v0.1.0
git push origin periplus-python-sdk-v0.1.0
```

PyPI publishing uses Trusted Publishing rather than a stored API token. The
PyPI publisher must be configured for GitHub owner `ekkuleivonen`, repository
`periplus`, workflow `python-sdk-release.yml`, and environment `pypi`. Protect
that GitHub environment with required reviewers before the first release.
