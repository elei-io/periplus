"""Stable Atlas SDK exception hierarchy."""


class AtlasSdkError(RuntimeError):
    """Base exception for Atlas SDK failures."""


class AtlasAuthenticationError(AtlasSdkError):
    """Atlas rejected or could not authenticate the supplied credentials."""


class AtlasPermissionDenied(AtlasSdkError):
    """The authenticated caller may not perform the requested operation."""


class AtlasInvalidRequest(AtlasSdkError):
    """Atlas rejected the request payload."""


class AtlasServiceUnavailable(AtlasSdkError):
    """Atlas could not return a trusted response."""


class AtlasProtocolError(AtlasSdkError):
    """Atlas returned a response that does not match the SDK protocol."""
