"""Repository object-store failures."""


class RepositoryError(RuntimeError):
    """Base class for repository failures."""


class RepositoryConfigError(RepositoryError):
    """Repository configuration is invalid."""


class RepositoryKeyError(RepositoryError, ValueError):
    """An object key is not a safe repository-relative key."""


class RepositoryObjectNotFound(RepositoryError, FileNotFoundError):
    """The requested repository object does not exist."""


class RepositoryIntegrityError(RepositoryError):
    """A repository object does not match its content-addressed identity."""
