"""Periplus DuckDB connection factories."""

from ._direct import DuckConfig, S3Config, duck
from ._factory import DuckLakeConnectionFactory
from ._protocol import DuckLakeConnectionProtocol

__all__ = [
    "DuckConfig",
    "DuckLakeConnectionFactory",
    "DuckLakeConnectionProtocol",
    "S3Config",
    "duck",
]
