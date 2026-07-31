"""Standalone Atlas SDK installation and end-to-end smoke proof."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import atlas_sdk
import duckdb


def _version(connection: duckdb.DuckDBPyConnection) -> str:
    row = connection.execute("SELECT web._catalogue_version()").fetchone()
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise AssertionError("Atlas catalogue version is unavailable")
    return row[0]


def _assert_installed_sdk() -> None:
    sdk_file = Path(atlas_sdk.__file__).resolve()
    repository_source = Path(__file__).resolve().parents[1] / "src"
    if sdk_file.is_relative_to(repository_source):
        raise AssertionError("smoke test imported repository SDK source")
    if "site-packages" not in sdk_file.parts:
        raise AssertionError("smoke test did not import an installed SDK")


async def main() -> None:
    _assert_installed_sdk()
    atlas_sdk.configure()
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = atlas_sdk.conn.duck()
        if not isinstance(connection, duckdb.DuckDBPyConnection):
            raise AssertionError("duck() did not return a DuckDB connection")
        _version(connection)

        crawl = await atlas_sdk.crawls.run(
            os.getenv("ATLAS_SMOKE_URL", "https://example.com/"),
            depth=0,
            max_crawls=1,
        )

        interrupted = asyncio.create_task(crawl.completed())
        await asyncio.sleep(0)
        interrupted.cancel()
        try:
            await interrupted
        except asyncio.CancelledError:
            pass
        await crawl.refresh()
        if crawl.status == "cancelled":
            raise AssertionError("cancelling a local wait cancelled the crawl")

        timeout = float(os.getenv("ATLAS_SMOKE_TIMEOUT_SECONDS", "180"))
        async with asyncio.timeout(timeout):
            await crawl.completed()
            crawl.raise_for_status()

        row = connection.execute(
            """
            SELECT count(*)
            FROM web.page_visit
            WHERE crawl_id = CAST(? AS UUID)
            """,
            [str(crawl.id)],
        ).fetchone()
        if row is None or row[0] < 1:
            raise AssertionError(
                "ingested crawl is not visible in web.page_visit"
            )
    finally:
        if connection is not None:
            connection.close()
        await atlas_sdk.aclose()

    print("Atlas SDK smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
