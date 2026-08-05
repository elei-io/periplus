"""Single command-line entrypoint for Periplus worker roles."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from importlib import import_module
import logging
from typing import Literal, cast

from periplus.platform.config import get_str


WorkerRole = Literal[
    "crawler",
    "ingestor",
    "materializer",
    "janitor",
]

WORKER_MODULES: dict[WorkerRole, str] = {
    "crawler": "periplus.crawl.crawler",
    "ingestor": "periplus.ingestion.ingestor",
    "materializer": "periplus.materialization.materializer",
    "janitor": "periplus.operations.janitor",
}


async def run(role: WorkerRole) -> None:
    module = import_module(WORKER_MODULES[role])
    runner = cast(Callable[[], Awaitable[None]], module.run)
    await runner()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a Periplus worker role.")
    parser.add_argument("role", choices=tuple(WORKER_MODULES))
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=get_str("PERIPLUS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(cast(WorkerRole, arguments.role)))
