from __future__ import annotations

import psycopg

from db import get_database_url

_CHANNEL = "atlas_task_runs_queued"


def _connection_string() -> str:
    return get_database_url().replace("postgresql+psycopg://", "postgresql://", 1)


class RunQueueListener:
    """Best-effort Postgres wake-ups; task claiming and polling remain authoritative."""

    def __init__(self, connection_string: str | None = None) -> None:
        self._connection_string = connection_string or _connection_string()
        self._connection: psycopg.AsyncConnection | None = None

    async def _connect(self) -> None:
        self._connection = await psycopg.AsyncConnection.connect(
            self._connection_string,
            autocommit=True,
            connect_timeout=1,
        )
        await self._connection.execute(f"LISTEN {_CHANNEL}")

    async def wait(self, timeout: float) -> bool:
        try:
            if self._connection is None or self._connection.closed:
                await self._connect()
            assert self._connection is not None
            async for _notification in self._connection.notifies(timeout=timeout, stop_after=1):
                return True
            return False
        except Exception:
            await self.close()
            return False

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None
