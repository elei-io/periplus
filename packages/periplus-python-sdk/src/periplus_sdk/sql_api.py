"""Notebook-friendly SQL engine over the public Periplus query API."""
from __future__ import annotations

from typing import Literal

from sqlalchemy import create_engine as _create_engine, event
from sqlalchemy.engine import Engine, URL


def create_engine(
    base_url: str | None = None,
    *,
    mode: Literal["stable", "experimental"] = "stable",
    timeout: float = 620,
    schema_version: str = "public_v1",
    allow_partial: bool = False,
) -> Engine:
    """Create a SQLAlchemy engine recognized by marimo and other SQL tools.

    The public URL defaults to PERIPLUS_PUBLIC_URL. Connections are opened lazily;
    dispose the engine when finished. Each query uses an independent server snapshot.
    """
    engine = _create_engine(
        URL.create("periplus", database="periplus"),
        connect_args={"base_url": base_url, "mode": mode, "timeout": timeout, "schema_version": schema_version, "allow_partial": allow_partial},
    )

    event.listen(engine, "before_execute", _parameters, retval=True)
    return engine


def _parameters(connection, clauseelement, multiparams, params, execution_options):
    bindings = execution_options.get("periplus_parameters", {})
    if bindings and not multiparams:
        names = clauseelement.compile().params
        params = {**{name: value for name, value in bindings.items() if name in names}, **params}
    return clauseelement, multiparams, params


def bind(engine: Engine, **parameters) -> Engine:
    """Create a marimo-discoverable engine with named SQL parameters for this cell.

    Uses the original engine's pool. No query, upload, or server state is created.
    Write :name placeholders in SQL cells; lists can be CAST(:ids AS VARCHAR[]).
    """
    from .dbapi import _parameter
    return engine.execution_options(periplus_parameters={name: _parameter(value) for name, value in parameters.items()})
