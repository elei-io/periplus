"""One serialized Basin catalogue session for mandatory API control work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import TypeVar

from fastapi import Request
from sqlalchemy.orm import Session

from db.session import session_scope
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.schema import CATALOGUE_SCHEMA_VERSION


T = TypeVar("T")
CatalogueOperation = Callable[[Session, Catalogue], T]


class CatalogueControl:
    """Own one session-affine DuckDB client on one thread for the API lifetime."""

    def __init__(
        self,
        factory: Callable[[], Catalogue] | None = None,
    ) -> None:
        self._factory = factory or (
            lambda: catalogue_from_env(threads=1, memory_limit="512MB")
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="atlas-api-catalogue",
        )
        self._catalogue: Catalogue | None = None

    async def start(self) -> None:
        await self._submit(self._open)

    async def close(self) -> None:
        try:
            await self._submit(self._close)
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)

    async def run(self, operation: CatalogueOperation[T]) -> T:
        return await self._submit(lambda: self._run(operation))

    async def latest_snapshot(self) -> int | None:
        return await self.run(lambda _session, catalogue: catalogue.latest_snapshot())

    async def _submit(self, operation: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, operation)

    def _open(self) -> None:
        if self._catalogue is not None:
            raise RuntimeError("API catalogue control is already started.")
        catalogue = self._factory()
        try:
            catalogue.validate_schema()
        except BaseException:
            catalogue.close()
            raise
        logging.info(
            "Atlas DuckLake binding ready: component=api lake=%s schemas=ingest,material "
            "schema_version=%s",
            catalogue.lake_slug,
            CATALOGUE_SCHEMA_VERSION,
        )
        self._catalogue = catalogue

    def _close(self) -> None:
        if self._catalogue is None:
            return
        self._catalogue.close()
        self._catalogue = None

    def _run(self, operation: CatalogueOperation[T]) -> T:
        if self._catalogue is None:
            raise RuntimeError("API catalogue control is not started.")
        with session_scope() as session:
            return operation(session, self._catalogue)


def get_catalogue_control(request: Request) -> CatalogueControl:
    control = getattr(request.app.state, "catalogue_control", None)
    if not isinstance(control, CatalogueControl):
        raise RuntimeError("API catalogue control is unavailable.")
    return control
