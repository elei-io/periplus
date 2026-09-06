"""One serialized DuckLake connection for API catalogue work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import TypeVar

import duckdb
from fastapi import Request
from sqlalchemy.orm import Session

from periplus.platform.postgres.session import session_scope
from periplus.platform.catalogue import Catalogue, catalogue_from_env
from periplus.platform.catalogue.schema import CATALOGUE_SCHEMA_VERSION
from periplus.platform.catalogue.public import PUBLIC_CATALOGUE_VERSION


T = TypeVar("T")
CatalogueOperation = Callable[[Session, Catalogue], T]
_INVALIDATING_ERRORS = (duckdb.FatalException, duckdb.InternalException)


class CatalogueControl:
    """Own one DuckDB connection on one thread for the API lifetime."""

    def __init__(
        self,
        factory: Callable[[], Catalogue] | None = None,
    ) -> None:
        self._factory = factory or (
            lambda: catalogue_from_env(threads=1, memory_limit="512MB")
        )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="periplus-api-catalogue",
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
        self._catalogue = self._new_catalogue()

    def _new_catalogue(self) -> Catalogue:
        catalogue = self._factory()
        try:
            catalogue.validate_schema()
        except BaseException:
            catalogue.close()
            raise
        logging.info(
            "Periplus DuckLake binding ready: component=api lake=%s "
            "schemas=ingest,material,web,content schema_version=%s "
            "public_catalogue_version=%s",
            catalogue.config.alias,
            CATALOGUE_SCHEMA_VERSION,
            PUBLIC_CATALOGUE_VERSION,
        )
        return catalogue

    def _close(self) -> None:
        if self._catalogue is None:
            return
        self._catalogue.close()
        self._catalogue = None

    def _run(self, operation: CatalogueOperation[T]) -> T:
        for attempt in range(2):
            if self._catalogue is None:
                raise RuntimeError("API catalogue control is not started.")
            try:
                with session_scope() as session:
                    return operation(session, self._catalogue)
            except _INVALIDATING_ERRORS:
                logging.exception(
                    "DuckLake invalidated the API catalogue connection; "
                    "opening a fresh attachment%s",
                    " and retrying once" if attempt == 0 else "",
                )
                self._replace_invalidated_catalogue()
                if attempt > 0:
                    raise
        raise AssertionError("unreachable catalogue retry state")

    def _replace_invalidated_catalogue(self) -> None:
        invalidated = self._catalogue
        self._catalogue = None
        if invalidated is not None:
            try:
                invalidated.close()
            except BaseException:
                logging.exception(
                    "Failed to close an invalidated API catalogue connection"
                )
        self._catalogue = self._new_catalogue()


def get_catalogue_control(request: Request) -> CatalogueControl:
    control = getattr(request.app.state, "catalogue_control", None)
    if not isinstance(control, CatalogueControl):
        raise RuntimeError("API catalogue control is unavailable.")
    return control
