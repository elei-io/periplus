"""Standalone Periplus SDK installation and end-to-end smoke proof."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import periplus_sdk
import duckdb


def _assert_installed_sdk() -> None:
    sdk_file = Path(periplus_sdk.__file__).resolve()
    repository_source = Path(__file__).resolve().parents[1] / "src"
    if sdk_file.is_relative_to(repository_source):
        raise AssertionError("smoke test imported repository SDK source")
    if "site-packages" not in sdk_file.parts:
        raise AssertionError("smoke test did not import an installed SDK")


async def main() -> None:
    _assert_installed_sdk()
    periplus_sdk.configure()
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = periplus_sdk.conn.duck()
        if not isinstance(connection, duckdb.DuckDBPyConnection):
            raise AssertionError("duck() did not return a DuckDB connection")
        connection.execute("DESCRIBE web.observation").fetchall()

        crawl = await periplus_sdk.crawls.run(
            os.getenv("PERIPLUS_SMOKE_URL", "https://example.com/"),
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

        timeout = float(os.getenv("PERIPLUS_SMOKE_TIMEOUT_SECONDS", "180"))
        async with asyncio.timeout(timeout):
            await crawl.completed()
            crawl.raise_for_status()

        row = connection.execute(
            """
            SELECT count(*)
            FROM web.observation
            WHERE crawl_id = CAST(? AS UUID)
            """,
            [str(crawl.id)],
        ).fetchone()
        if row is None or row[0] < 1:
            raise AssertionError(
                "ingested crawl is not visible in web.observation"
            )
    finally:
        if connection is not None:
            connection.close()
        await periplus_sdk.aclose()

    print("Periplus SDK smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
