"""Public compiler-backed helpers for bounded crawl-graph edge SQL."""

from atlas_sql import AtlasCompiler, GraphEdgePurpose, graph_edge_uses_catalogue


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


def edge_uses_catalogue(sql: str) -> bool:
    return graph_edge_uses_catalogue(sql)
