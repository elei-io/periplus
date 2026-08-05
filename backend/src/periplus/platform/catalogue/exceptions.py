"""DuckLake repository errors."""


class CatalogueError(Exception):
    """Base class for Periplus catalogue failures."""


class CatalogueConfigError(CatalogueError, ValueError):
    """Raised when Periplus catalogue configuration is invalid."""


class CatalogueSchemaError(CatalogueError):
    """Raised when the attached DuckLake schema differs from Periplus's contract."""


class CatalogueConflictError(CatalogueError):
    """Raised when an immutable catalogue identity has different content."""


class CatalogueValidationError(CatalogueError, ValueError):
    """Raised when a catalogue write violates Periplus repository invariants."""
