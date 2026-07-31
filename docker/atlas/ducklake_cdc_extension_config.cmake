# Keep Atlas linked exactly as in the cached base build. Build DuckLake CDC as a
# loadable extension without changing DuckDB's linked extension graph.
duckdb_extension_load(atlas
    SOURCE_DIR /build/atlas-extension
)

duckdb_extension_load(ducklake_cdc
    SOURCE_DIR /build/ducklake-cdc-extension
    DONT_LINK
)
