# Atlas Python SDK

The first public SDK surface is intentionally small:

```python
import atlas_sdk

atlas_sdk.configure(api_url="http://localhost:8000")

managed = atlas_sdk.conn.quack()
direct = atlas_sdk.conn.duck()

crawl = await atlas_sdk.crawls.run(
    "https://example.com/",
    depth=0,
    max_crawls=1,
)
await crawl.completed()
crawl.raise_for_status()
await crawl.materialized()
```

The PyPI distribution is named `atlas-python-sdk`; its Python import package
is `atlas_sdk`. This keeps it safe to install alongside the Atlas backend,
whose import package is `atlas`.

`quack()` and `duck()` return ordinary `duckdb.DuckDBPyConnection`
instances. `quack()` uses the managed DuckBasin/Quack connection boundary;
`duck()` is the development-only direct DuckLake attachment boundary.

Lifecycle waits are local, cancellable polling operations. Timing out or
cancelling a wait never cancels the server-side crawl; call `crawl.cancel()`
explicitly for that transition. `materialized()` waits for the ingestion
writes belonging to that crawl and for the fixed document and visit
projections to acknowledge those exact DuckLake snapshots.

Install the released package from PyPI:

```sh
python -m pip install atlas-python-sdk
```

For local development, install directly from the repository:

```sh
python -m pip install ./packages/atlas-python-sdk
```

The standalone end-to-end proof is `examples/smoke.py`. It uses
`ATLAS_API_URL`, the standard `DUCKBASIN_*` managed connection variables,
and either the direct connection variables or `ATLAS_DIRECT_ENV_FILE`.

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
