"""Small Atlas-owned types for declaring the managed DuckLake schema."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MapType:
    key_type: str
    value_type: str

    def sql(self) -> str:
        return f"MAP({self.key_type}, {self.value_type})"


@dataclass(frozen=True, slots=True)
class ColumnDef:
    data_type: str | MapType
    nullable: bool = True
