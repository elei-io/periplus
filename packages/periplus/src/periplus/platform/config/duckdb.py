"""Validated per-process limits applied to every managed DuckDB connection."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from periplus.platform.config.environment import get_optional


class DuckDBLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threads: int | None = Field(default=None, ge=1, le=1024)
    memory_limit: str | None = Field(
        default=None, pattern=r"^[1-9][0-9]*(?:[KMGTP]i?B|B)$"
    )
    max_temp_directory_size: str | None = Field(
        default=None, pattern=r"^(?:0|[1-9][0-9]*)(?:[KMGTP]i?B|B)$"
    )


def connection_limits(defaults: Mapping[str, str] | None = None) -> dict[str, str]:
    """Process settings override caller defaults, including coordinator connections.

    Limits are per connection, not a shared process pool. An unset value keeps
    the caller's existing bound; Helm supplies explicit bounds for every role.
    """
    limits = DuckDBLimits.model_validate({
        "threads": get_optional("PERIPLUS_DUCKDB_THREADS"),
        "memory_limit": get_optional("PERIPLUS_DUCKDB_MEMORY_LIMIT"),
        "max_temp_directory_size": get_optional("PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE"),
    })
    return dict(defaults or {}) | {
        name: str(value) for name, value in limits.model_dump(exclude_none=True).items()
    }
