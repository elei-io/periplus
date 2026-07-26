"""Single command-line entrypoint for Atlas worker roles."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from importlib import import_module
import logging
from typing import Literal, cast

from config import get_str


WorkerRole = Literal[
    "acquisition",
    "ingestion",
    "cdc",
    "materialization",
    "housekeeping",
]

WORKER_MODULES: dict[WorkerRole, str] = {
    "acquisition": "workers.acquisition",
    "ingestion": "workers.ingestion",
    "cdc": "cdc.worker",
    "materialization": "workers.materialization",
    "housekeeping": "workers.housekeeping",
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
