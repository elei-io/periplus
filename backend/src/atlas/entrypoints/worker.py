"""Single command-line entrypoint for Atlas worker roles."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from importlib import import_module
import logging
from typing import Literal, cast

from atlas.platform.config import get_str


WorkerRole = Literal[
    "crawler",
    "ingestor",
    "materializer",
    "janitor",
]

WORKER_MODULES: dict[WorkerRole, str] = {
    "crawler": "atlas.crawl.crawler",
    "ingestor": "atlas.ingestion.ingestor",
    "materializer": "atlas.materialization.materializer",
    "janitor": "atlas.operations.janitor",
}


async def run(role: WorkerRole) -> None:
    module = import_module(WORKER_MODULES[role])
    runner = cast(Callable[[], Awaitable[None]], module.run)
    await runner()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run an Atlas worker role.")
    parser.add_argument("role", choices=tuple(WORKER_MODULES))
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=get_str("ATLAS_LOG_LEVEL"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(cast(WorkerRole, arguments.role)))
