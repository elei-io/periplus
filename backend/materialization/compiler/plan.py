"""Immutable output of materialization compilation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaterializationRefreshStrategy(StrEnum):
    KEYED = "keyed"
    APPEND = "append"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class MaterializationScan:
    """A physical relation scan whose key lineage has been proven."""

    relation: str
    alias: str
    key_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    """Compiler-owned plan, independent of API and worker transport."""

    compiler_version: int
    source_sql: str
    normalized_sql: str
    source_table: str
    refresh_strategy: MaterializationRefreshStrategy
    key_columns: tuple[str, ...]
    output_columns: tuple[str, ...]
    scans: tuple[MaterializationScan, ...]

    def explain(self) -> str:
        """Render a compact developer-facing explanation of the proof."""

        keys = ", ".join(self.key_columns)
        scans = "\n".join(
            f"  {scan.relation} AS {scan.alias} by "
            f"{', '.join(scan.key_columns)}"
            for scan in self.scans
        )
        return (
            f"{self.refresh_strategy.value} materialization supported\n"
            f"Driving table: {self.source_table}\n"
            f"Stable key: {keys}\n"
            f"Bounded scans:\n{scans}"
        )
