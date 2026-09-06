# DuckLake CDC extension development

Periplus uses standard DuckDB and has no custom query extension. Query validation and future
optimizations belong behind the Python query API; see [QUERY.md](QUERY.md).

The separately maintained DuckLake CDC extension is used only by live materialization. Its
source and DuckDB version are pinned in `.github/extension-sources.env`. Local Compose finds it
through `PERIPLUS_DUCKLAKE_CDC_EXTENSION_REPO`; the core image compiles and embeds the artifact.
Keep its DuckDB ABI aligned with the Python DuckDB version. Follow the CDC repository's own
build/test instructions when changing that extension.

`./ducklake.sh` uses the standard `duckdb` CLI on PATH and attaches the configured lake read-only.
It does not require a custom extension build or a sibling Periplus extension checkout.
