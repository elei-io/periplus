"""Public catalogue-query compiler contract."""

from .compiler import (
    compile_catalogue_query,
    compile_interactive_catalogue_query,
)
from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .metadata import (
    BoundParameter,
    CatalogueMetadataSnapshot,
    ColumnStatistics,
    CompilationEstimate,
    ManagedTableMetadata,
    PartitionColumn,
    ScanEstimate,
)
from .lint import (
    CatalogueLintDiagnostic,
    ClassifiedCatalogueStatement,
    CatalogueStatementKind,
    classify_catalogue_statement,
    lint_catalogue_statement,
    lint_select,
)
from .graph_edge import graph_edge_uses_catalogue
from .purpose import (
    CatalogueDefinitionPurpose,
    FullMaterializationPurpose,
    GraphEdgePurpose,
    InteractiveQueryPurpose,
    KeyedMaterializationPurpose,
    ScalarMacroDefinition,
    ScalarFunctionDefinition,
    TableMacroDefinition,
    ViewDefinition,
)
from .result import (
    SqlAppliedRewrite,
    SqlCompilationDiagnostic,
    SqlCompilationOutcome,
    SqlCompilationPurpose,
    SqlCompilationResult,
    compile_catalogue_sql,
)
from .syntax import CatalogueQueryError, classify_select

__all__ = [
    "CatalogueDefinitionPurpose",
    "CatalogueMetadataSnapshot",
    "CatalogueLintDiagnostic",
    "CatalogueQueryError",
    "CatalogueStatementKind",
    "ClassifiedCatalogueStatement",
    "BoundParameter",
    "ColumnStatistics",
    "CompilationEstimate",
    "FullMaterializationPurpose",
    "GraphEdgePurpose",
    "KeyedMaterializationPurpose",
    "InteractiveQueryPurpose",
    "ManagedTableMetadata",
    "OptimizationCode",
    "OptimizationDiagnostic",
    "QueryOptimizationUnavailable",
    "PartitionColumn",
    "ScalarMacroDefinition",
    "ScalarFunctionDefinition",
    "SqlAppliedRewrite",
    "SqlCompilationDiagnostic",
    "SqlCompilationOutcome",
    "SqlCompilationPurpose",
    "SqlCompilationResult",
    "ScanEstimate",
    "TableMacroDefinition",
    "ViewDefinition",
    "compile_catalogue_query",
    "classify_catalogue_statement",
    "classify_select",
    "compile_catalogue_sql",
    "compile_interactive_catalogue_query",
    "graph_edge_uses_catalogue",
    "lint_catalogue_statement",
    "lint_select",
]
