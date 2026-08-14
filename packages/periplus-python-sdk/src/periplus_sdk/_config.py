"""Process-local notebook configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ApiConfig:
    api_url: str
    api_token: str | None = field(default=None, repr=False)


_configured: ApiConfig | None = None


def configure(
    *,
    api_url: str | None = None,
    api_token: str | None = None,
) -> None:
    """Set the default Periplus control-plane connection."""

    global _configured
    resolved_url = (api_url or os.getenv("PERIPLUS_API_URL", "")).strip()
    if not resolved_url:
        raise ConfigurationError(
            "Periplus API URL is required; pass api_url or set PERIPLUS_API_URL"
        )
    _configured = ApiConfig(
        api_url=resolved_url.rstrip("/"),
        api_token=api_token or os.getenv("PERIPLUS_API_TOKEN"),
    )


def api_config() -> ApiConfig:
    if _configured is not None:
        return _configured
    configure()
    assert _configured is not None
    return _configured
