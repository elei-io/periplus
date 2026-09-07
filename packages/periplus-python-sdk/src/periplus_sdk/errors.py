"""Public Periplus SDK exceptions."""

from __future__ import annotations


class PeriplusError(Exception):
    """Base class for Periplus SDK failures."""


class ConfigurationError(PeriplusError):
    """Required SDK configuration is missing or invalid."""


class PeriplusConnectionError(PeriplusError):
    """A Periplus DuckDB connection could not be established."""


class AuthenticationError(PeriplusConnectionError):
    """Periplus rejected the supplied credentials."""


class ApiError(PeriplusError):
    """The Periplus control-plane API rejected a request."""

    def __init__(self, message: str, *, status_code: int, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class NotFoundError(ApiError):
    """The requested Periplus resource does not exist."""


class ConflictError(ApiError):
    """The requested transition conflicts with current state."""


class ValidationError(ApiError):
    """Periplus rejected invalid user input."""


class WaitTimeout(PeriplusError, TimeoutError):
    """A local lifecycle wait exceeded its timeout."""


class CollectionFailed(PeriplusError):
    """A collection settled unsuccessfully or supplied failed pages."""


class CatalogueVersionError(PeriplusConnectionError):
    """The attached lake does not expose a valid Periplus catalogue."""
