"""Shared CLI cache-control input helpers."""

from __future__ import annotations

import typer


def cache_input(
    *,
    refresh: bool,
    no_store: bool,
    max_cache_age: int | None,
    stale_if_error: int | None,
) -> dict[str, object] | None:
    if refresh and no_store:
        raise typer.BadParameter("--refresh and --no-store cannot be used together")
    values: dict[str, object] = {}
    if refresh:
        values["mode"] = "refresh"
    elif no_store:
        values["mode"] = "no_store"
    if max_cache_age is not None:
        values["max_age_seconds"] = max_cache_age
    if stale_if_error is not None:
        values["stale_if_error_seconds"] = stale_if_error
    return values or None
