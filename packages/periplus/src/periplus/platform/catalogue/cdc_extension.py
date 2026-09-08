"""Install and validate the signed community CDC package for this runtime."""

import duckdb

DUCKDB_VERSION = "1.5.5"
CDC_VERSION = "ducklake_cdc 0.6.3"
CDC_REVISION = "f909296"


def load_cdc_extension(connection: duckdb.DuckDBPyConnection) -> None:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError(f"CDC requires DuckDB {DUCKDB_VERSION}, got {duckdb.__version__}")
    connection.execute("INSTALL ducklake_cdc FROM community")
    connection.execute("LOAD ducklake_cdc")
    actual = connection.execute("SELECT cdc_version(), cdc_build_revision()").fetchone()
    if actual != (CDC_VERSION, CDC_REVISION):
        raise RuntimeError(
            f"Expected CDC {(CDC_VERSION, CDC_REVISION)!r}, got {actual!r}; "
            "review and update the pinned community package before upgrading"
        )


if __name__ == "__main__":
    with duckdb.connect(config={"allow_unsigned_extensions": "false"}) as connection:
        load_cdc_extension(connection)
