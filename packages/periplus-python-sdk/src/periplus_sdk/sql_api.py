"""Notebook-friendly SQL engine over the public Periplus query API."""
from __future__ import annotations

from typing import Literal

from sqlalchemy import create_engine as _create_engine
from sqlalchemy.engine import Engine, URL


def create_engine(
    base_url: str | None = None,
    *,
    mode: Literal["stable", "experimental"] = "stable",
    timeout: float = 140,
    schema_version: str = "public_v1",
) -> Engine:
    """Create a SQLAlchemy engine recognized by marimo and other SQL tools.

    The public URL defaults to PERIPLUS_PUBLIC_URL. Connections are opened lazily;
    dispose the engine when finished. Each query uses an independent server snapshot.
    """
    return _create_engine(
        URL.create("periplus", database=schema_version),
        connect_args={"base_url": base_url, "mode": mode, "timeout": timeout},
    )
