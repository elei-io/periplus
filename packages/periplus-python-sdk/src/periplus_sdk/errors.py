"""Public query failures, including the server's safe error category."""


class PeriplusError(Exception):
    """Base SDK error."""


class ConfigurationError(PeriplusError, ValueError):
    """The public application URL or timeout is invalid."""


class TransportError(PeriplusError):
    """The public application could not be reached; no automatic retry occurs."""


class ResponseError(PeriplusError):
    """The public application returned an invalid response."""


class ApiError(PeriplusError):
    """A public gateway or query-service rejection."""

    def __init__(self, message: str, *, status_code: int, code: str | None = None,
                 retry_after_seconds: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds
