"""Public Atlas SDK exceptions."""

from __future__ import annotations


class AtlasError(Exception):
    """Base class for Atlas SDK failures."""


class ConfigurationError(AtlasError):
    """Required SDK configuration is missing or invalid."""


class AtlasConnectionError(AtlasError):
    """An Atlas DuckDB connection could not be established."""


class AuthenticationError(AtlasConnectionError):
    """Atlas rejected the supplied credentials."""


class ApiError(AtlasError):
    """The Atlas control-plane API rejected a request."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(ApiError):
    """The requested Atlas resource does not exist."""


class ConflictError(ApiError):
    """The requested transition conflicts with current state."""


class ValidationError(ApiError):
    """Atlas rejected invalid user input."""


class WaitTimeout(AtlasError, TimeoutError):
    """A local lifecycle wait exceeded its timeout."""


class CrawlFailed(AtlasError):
    """A crawl reached an unsuccessful terminal state."""


class CatalogueVersionError(AtlasConnectionError):
    """The attached lake does not expose a valid Atlas catalogue."""


class ExtensionVersionError(AtlasConnectionError):
    """The requested Atlas DuckDB extension cannot be loaded."""
