"""Atlas DuckDB connection factories."""

from ._common import ExtensionMode, QueryProfile
from ._direct import DuckConfig, duck

__all__ = [
    "DuckConfig",
    "ExtensionMode",
    "QueryProfile",
    "duck",
]
