"""Atlas browser acquisition worker entrypoint."""

import argparse
import asyncio

from crawl4ai import AsyncWebCrawler

from actions.shared.crawl import browser_config_for_mode
from workers.acquisition import run


async def _run() -> None:
    crawler = AsyncWebCrawler(config=browser_config_for_mode("app"))
    await crawler.start()
    try:
        await run("browser", crawler=crawler)
    finally:
        await crawler.close()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas browser acquisition worker.").parse_args()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
