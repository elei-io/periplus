"""Immutable internal plans for catalogue-query optimization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RelationKeyBinding:
    source_column: str
    relation_column: str


@dataclass(frozen=True, slots=True)
class CatalogueScan:
    ordinal: int
    relation: str
    alias: str
    key_bindings: tuple[RelationKeyBinding, ...]


@dataclass(frozen=True, slots=True)
class CatalogueQueryPlan:
    compiler_version: int
    normalized_sql: str
    scans: tuple[CatalogueScan, ...]
