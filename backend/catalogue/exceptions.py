"""Catalogue-specific errors."""


class CatalogueError(Exception):
    """Base class for Atlas catalogue failures."""


class CatalogueConfigError(CatalogueError, ValueError):
    """Raised when Atlas catalogue configuration is invalid."""


class CatalogueSchemaError(CatalogueError):
    """Raised when the attached DuckLake schema differs from Atlas's contract."""


class CatalogueConflictError(CatalogueError):
    """Raised when an immutable catalogue identity has different content."""


class CatalogueValidationError(CatalogueError, ValueError):
    """Raised when a catalogue write violates Atlas repository invariants."""
