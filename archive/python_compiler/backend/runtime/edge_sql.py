"""Public compiler-backed helpers for bounded crawl-graph edge SQL."""

from dataclasses import dataclass

from atlas_sql import AtlasCompiler, GraphEdgePurpose, graph_edge_uses_catalogue


@dataclass(frozen=True, slots=True)
class FrozenEdgeSql:
    executable_sql: str
    uses_catalogue: bool
    catalogue_revision: str | None


def compile_edge_sql(
    sql: str,
    *,
    purpose: GraphEdgePurpose | None = None,
) -> str:
    """Return executable edge SQL or reject an invalid edge contract."""

    result = AtlasCompiler.embedded().compile(
        sql,
        purpose=purpose or GraphEdgePurpose(),
        coverage_source="graph_edge",
    )
    if not result.valid:
        message = next(
            (
                diagnostic.message
                for diagnostic in result.diagnostics
                if diagnostic.severity == "error"
            ),
            "Edge SQL is invalid.",
        )
        raise ValueError(message)
    if result.executable_sql is None:
        raise RuntimeError("Valid edge compilation did not return executable SQL.")
    return result.executable_sql


def freeze_edge_sql(
    sql: str,
    *,
    purpose: GraphEdgePurpose | None = None,
    catalogue_revision: str | None = None,
) -> FrozenEdgeSql:
    """Compile and classify one edge exactly once for a frozen graph run."""

    result = AtlasCompiler.embedded(
        catalogue_revision=catalogue_revision,
    ).compile(
        sql,
        purpose=purpose or GraphEdgePurpose(),
        coverage_source="graph_edge",
    )
    if not result.valid or result.executable_sql is None:
        message = next(
            (
                diagnostic.message
                for diagnostic in result.diagnostics
                if diagnostic.severity == "error"
            ),
            "Edge SQL is invalid.",
        )
        raise ValueError(message)
    executable_sql = result.executable_sql
    uses_catalogue = graph_edge_uses_catalogue(executable_sql)
    used_definitions = any(
        rewrite.rule == "catalogue_definition_expansion"
        for rewrite in result.applied_rewrites
    )
    if uses_catalogue and not result.supported:
        raise ValueError(
            "Catalogue-backed edge SQL must compile against the frozen "
            "catalogue definitions before a graph run starts."
        )
    return FrozenEdgeSql(
        executable_sql=executable_sql,
        uses_catalogue=uses_catalogue,
        catalogue_revision=(
            catalogue_revision
            if uses_catalogue or used_definitions
            else None
        ),
    )


def edge_uses_catalogue(sql: str) -> bool:
    return graph_edge_uses_catalogue(sql)
