"""Installed SDK proof: collection, immutable fulfillment, and applicable materialization."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Timer

import periplus_sdk
from periplus_sdk import CollectionSpec


def _assert_installed_sdk() -> None:
    sdk_file = Path(periplus_sdk.__file__).resolve()
    repository_source = Path(__file__).resolve().parents[1] / "src"
    if sdk_file.is_relative_to(repository_source) or "site-packages" not in sdk_file.parts:
        raise AssertionError("smoke proof requires an installed SDK wheel")


def _queryable(connection, identity):
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        row = connection.execute("""
            SELECT count(DISTINCT o.observation_id)
            FROM web.fulfillment f
            JOIN web.observation o USING (observation_id)
            JOIN content.object c USING (content_id)
            WHERE f.collection_id = CAST(? AS UUID) AND o.outcome = 'succeeded'
              AND (c.content_format <> 'html' OR EXISTS (
                  SELECT 1 FROM content.html_element e
                  WHERE e.content_id = c.content_id AND e.element_index = 0
              ))
        """, [str(identity)]).fetchone()
        return row is not None and row[0] > 0
    finally:
        timer.cancel()
        timer.join()


async def main() -> None:
    _assert_installed_sdk()
    periplus_sdk.configure()
    connection = periplus_sdk.conn.duck()
    reading = None
    try:
        collection = await periplus_sdk.collections.submit(CollectionSpec(
            seed_urls=(os.getenv("PERIPLUS_SMOKE_URL", "https://example.com/"),),
            max_depth=0, page_limit=1, result_max_age_seconds=0,
        ))
        interrupted = asyncio.create_task(collection.wait())
        await asyncio.sleep(0)
        interrupted.cancel()
        try:
            await interrupted
        except asyncio.CancelledError:
            pass
        await collection.refresh()
        if collection.snapshot.outcome == "cancelled":
            raise AssertionError("cancelling a local wait cancelled the collection")
        timeout = float(os.getenv("PERIPLUS_SMOKE_TIMEOUT_SECONDS", "180"))
        async with asyncio.timeout(timeout):
            await collection.wait()
            collection.raise_for_status()
            # Settlement does not establish ingestion or structural query readiness.
            while True:
                reading = asyncio.create_task(asyncio.to_thread(_queryable, connection, collection.id))
                if await asyncio.shield(reading):
                    break
                await asyncio.sleep(2)
    finally:
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
        connection.close()
    print("Periplus SDK smoke proof passed")


if __name__ == "__main__":
    asyncio.run(main())
