# DuckLake CDC extension distribution

Periplus uses standard DuckDB and has no custom query extension. Query validation and future
optimizations belong behind the Python query API; see [QUERY.md](QUERY.md).

Live materialization alone loads the signed `ducklake_cdc` community extension. The core image
installs it with `INSTALL ducklake_cdc FROM community` during build; runtime uses that cached
package without a source checkout, native compilation, or unsigned-extension permission.
Host-side materializers install the same package on first use.

`packages/periplus/src/periplus/platform/catalogue/cdc_extension.py` pins and validates DuckDB
1.5.5, CDC 0.6.3, and source revision `f909296` at image build and CDC connection startup.
When upgrading, update those pins together with the Python DuckDB dependency and lockfile,
verify the community artifact on the deployment platforms, and rebuild the immutable core image.
An unexpected community version fails validation; runtime does not automatically update it.

Setup scopes `cdc_doctor` to the active generation when one exists. Consumer
existence uses the native `cdc_consumer_stats(..., consumer := name)` argument.
Unscoped doctor/list calls inspect historical consumers' pending schema boundaries
and can exceed worker transaction limits on a mature lake. A SQL WHERE applied
after `cdc_list_consumers` does not restrict that internal work. Full historical
diagnostics remain an explicit operator action; setup does not retire/reset cursors.

The upstream source lives at https://github.com/elei-io/ducklake-cdc-extension. Follow that
repository's build/test instructions when developing CDC itself, then publish through community.

`./ducklake.sh` uses the standard `duckdb` CLI on PATH and attaches the configured lake read-only.
It does not require a custom extension build or any sibling extension checkout.
