"""Shared types and constants for physical DuckLake relations."""

from __future__ import annotations

from dataclasses import dataclass


CATALOGUE_SCHEMA_VERSION = "4.0.0"
INGEST_SCHEMA = "ingest"
MATERIAL_SCHEMA = "material"
PARTITION_BUCKETS = 64


@dataclass(frozen=True, slots=True, order=True)
class RelationName:
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True, slots=True)
class TableLayout:
    partition_by: tuple[str, ...] = ()
    sort_by: tuple[str, ...] = ()
