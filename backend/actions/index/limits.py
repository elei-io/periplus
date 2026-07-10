"""Deployment ceilings for caller-controlled index workset budgets."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MAX_PAGES = 100_000
DEFAULT_MAX_LINKS = 5_000_000
DEFAULT_MAX_TEMP_BYTES = 10 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IndexLimits:
    max_pages: int = DEFAULT_MAX_PAGES
    max_links: int = DEFAULT_MAX_LINKS
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES

    @classmethod
    def from_env(cls) -> IndexLimits:
        return cls(
            max_pages=_positive_int("ATLAS_INDEX_MAX_PAGES", DEFAULT_MAX_PAGES),
            max_links=_positive_int("ATLAS_INDEX_MAX_LINKS", DEFAULT_MAX_LINKS),
            max_temp_bytes=_positive_int(
                "ATLAS_INDEX_MAX_TEMP_BYTES",
                DEFAULT_MAX_TEMP_BYTES,
            ),
        )

    def validate(
        self,
        *,
        max_pages: int,
        max_links: int,
        max_temp_bytes: int,
    ) -> None:
        requested = {
            "max_pages": (max_pages, self.max_pages, "ATLAS_INDEX_MAX_PAGES"),
            "max_links": (max_links, self.max_links, "ATLAS_INDEX_MAX_LINKS"),
            "max_temp_bytes": (
                max_temp_bytes,
                self.max_temp_bytes,
                "ATLAS_INDEX_MAX_TEMP_BYTES",
            ),
        }
        for name, (value, ceiling, environment_name) in requested.items():
            if value > ceiling:
                raise ValueError(
                    f"{name}={value} exceeds the deployment ceiling {ceiling} "
                    f"from {environment_name}"
                )


def validate_index_budgets(
    *,
    max_pages: int,
    max_links: int,
    max_temp_bytes: int,
) -> None:
    IndexLimits.from_env().validate(
        max_pages=max_pages,
        max_links=max_links,
        max_temp_bytes=max_temp_bytes,
    )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
