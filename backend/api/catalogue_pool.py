"""Bounded long-lived read connections for interactive catalogue SQL."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Queue

from repository.catalogue import Catalogue, catalogue_config_from_env


class CatalogueReadPoolExhausted(RuntimeError):
    """Raised when no interactive read connection becomes available in time."""


class CatalogueReadPool:
    """Own validated catalogue connections and lease each to one request at a time."""

    def __init__(
        self,
        size: int,
        *,
        threads: int,
        wait_timeout_seconds: float,
        factory: Callable[[], Catalogue] | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError("catalogue read pool size must be greater than zero")
        if threads <= 0:
            raise ValueError("catalogue read threads must be greater than zero")
        if wait_timeout_seconds <= 0:
            raise ValueError("catalogue read pool wait timeout must be greater than zero")
        self.size = size
        self.threads = threads
        self.wait_timeout_seconds = wait_timeout_seconds
        self._factory = factory or (lambda: Catalogue(catalogue_config_from_env()))
        self._available: Queue[Catalogue] = Queue(maxsize=size)
        self._catalogues: list[Catalogue] = []

    def open(self) -> None:
        """Create and validate every connection before the API accepts requests."""

        if self._catalogues:
            raise RuntimeError("catalogue read pool is already open")
        try:
            for _ in range(self.size):
                catalogue = self._factory()
                catalogue.connection.execute(f"SET threads = {self.threads}")
                catalogue.validate_schema()
                self._catalogues.append(catalogue)
                self._available.put_nowait(catalogue)
        except Exception:
            self.close()
            raise

    def acquire(self) -> Catalogue:
        """Wait for one exclusively leased read connection."""

        if not self._catalogues:
            raise RuntimeError("catalogue read pool is not open")
        try:
            return self._available.get(timeout=self.wait_timeout_seconds)
        except Empty as exc:
            raise CatalogueReadPoolExhausted(
                "catalogue SQL is busy; no read connection became available "
                f"within {self.wait_timeout_seconds:g} seconds"
            ) from exc

    def release(self, catalogue: Catalogue) -> None:
        if catalogue not in self._catalogues:
            raise ValueError("catalogue does not belong to this read pool")
        self._available.put_nowait(catalogue)

    def close(self) -> None:
        while not self._available.empty():
            self._available.get_nowait()
        catalogues, self._catalogues = self._catalogues, []
        for catalogue in catalogues:
            catalogue.close()
