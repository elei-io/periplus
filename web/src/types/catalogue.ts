export type CatalogueStatementKind = "query" | "explain" | "explain_analyze"
export type CompilationOutcome =
  | "invalid"
  | "optimized"
  | "unchanged"
  | "unsupported"
export type CompilationDiagnostic = {
  code: string
  severity: "warning" | "error"
  message: string
  sql_fragment: string | null
  documentation_anchor: string | null
}
export type DefinitionDependency = {
  kind: string
  qualified_name: string
  path: string[]
}

export type CatalogueQueryRequest = {
  sql: string
}

export type CatalogueQueryResult = {
  statementKind: CatalogueStatementKind
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
}

export type CatalogueQueryState = {
  id: string
  statement_kind: CatalogueStatementKind
  optimization_status: "optimized" | "unchanged" | "degraded_fallback"
  applied_rewrites: {
    rule: string
    evidence: string
  }[]
  optimization_diagnostics: {
    code: string
    severity: "info" | "warning"
    message: string
    documentation_anchor: string | null
  }[]
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled"
  created_at: string
  started_at: string | null
  completed_at: string | null
  cancel_requested_at: string | null
  row_count: number
  result_bytes: number
  error: string | null
}

export type CatalogueMetadataColumn = {
  name: string
  data_type: string
  nullable: boolean
}

export type CatalogueMetadataRelation = {
  catalog_name: string
  schema_name: string
  name: string
  kind: "table" | "view"
  columns: CatalogueMetadataColumn[]
}

export type CatalogueMetadataFunctionParameter = {
  name: string
  data_type: string | null
}

export type CatalogueMetadataFunction = {
  catalog_name: string
  schema_name: string
  name: string
  kind: string
  description: string | null
  return_type: string | null
  parameters: CatalogueMetadataFunctionParameter[]
  varargs: string | null
  result_columns: CatalogueMetadataColumn[]
}

export type CatalogueMetadata = {
  catalog_name: string
  default_schema: string
  relations: CatalogueMetadataRelation[]
  functions: CatalogueMetadataFunction[]
}

export type CatalogueLintDiagnostic = {
  code: string
  severity: "warning" | "error"
  message: string
  documentation_anchor: string | null
}

export type CatalogueLintResult = {
  valid: boolean
  supported: boolean
  materialization_eligible: boolean
  outcome: "invalid" | "optimized" | "unchanged" | "unsupported"
  authored_sql: string
  executable_sql: string | null
  diagnostics: CatalogueLintDiagnostic[]
  applied_rewrites: Array<{ rule: string; evidence: string }>
  dependencies: DefinitionDependency[]
  catalogue_revision: string | null
  compiler_version: string
}

export type MaterializationEligibility = {
  eligible: boolean
  diagnostics: Array<{
    code: string
    severity: "warning" | "error"
    message: string
  }>
}

export type CatalogueMaterializationSummary = {
  id: string
  status: MaterializationObservedState
}

export type MaterializationObservedState =
  | "creating"
  | "backfilling"
  | "live"
  | "paused"
  | "deleting"
  | "blocked_schema"
  | "failed"

export type CatalogueViewRecord = {
  id: string | null
  ducklake_view_uuid: string
  schema_name: string
  view_name: string
  qualified_name: string
  slug: string
  description: string | null
  fixture_path: string | null
  sql: string
  columns: string[]
  column_types: string[]
  managed: boolean
  available: boolean
  created_at: string | null
  updated_at: string | null
  created_from_query_revision_id: string | null
  materialization: CatalogueMaterializationSummary | null
  compiler_outcome: CompilationOutcome | null
  compiler_diagnostics: CompilationDiagnostic[]
  compiler_dependencies: DefinitionDependency[]
  compiler_version: string | null
  catalogue_definition_revision: string | null
}

export type CatalogueViewList = { items: CatalogueViewRecord[] }

export type CatalogueTableMacroRecord = {
  id: string
  kind: "table"
  schema_name: string
  macro_name: string
  qualified_name: string
  slug: string
  description: string | null
  parameters: string[]
  parameter_defaults: Record<string, string>
  sql: string
  definition_revision_id: string
  fixture_path: string | null
  available: boolean
  created_from_query_revision_id: string | null
  created_at: string
  updated_at: string
  compiler_outcome: CompilationOutcome | null
  compiler_diagnostics: CompilationDiagnostic[]
  compiler_dependencies: DefinitionDependency[]
  compiler_version: string | null
  catalogue_definition_revision: string | null
}

export type CatalogueTableMacroList = { items: CatalogueTableMacroRecord[] }

export type CatalogueScalarMacroRecord = {
  id: string
  kind: "scalar"
  schema_name: string
  macro_name: string
  qualified_name: string
  slug: string
  description: string | null
  parameters: string[]
  sql: string
  definition_revision_id: string
  fixture_path: string | null
  available: boolean
  created_at: string
  updated_at: string
  compiler_outcome: CompilationOutcome | null
  compiler_diagnostics: CompilationDiagnostic[]
  compiler_dependencies: DefinitionDependency[]
  compiler_version: string | null
  catalogue_definition_revision: string | null
}

export type CatalogueScalarMacroList = { items: CatalogueScalarMacroRecord[] }
export type CatalogueMacroRecord =
  CatalogueScalarMacroRecord | CatalogueTableMacroRecord

export type SavedQueryRevision = {
  id: string
  query_id: string
  revision: number
  sql: string
  sql_hash: string
  change_note: string | null
  compiler_outcome: CompilationOutcome
  compiler_diagnostics: CompilationDiagnostic[]
  compiler_dependencies: DefinitionDependency[]
  compiler_version: string
  catalogue_definition_revision: string | null
  created_at: string
}

export type SavedQuery = {
  id: string
  slug: string
  description: string | null
  fixture_path: string | null
  current_revision_id: string
  current_revision: number
  sql: string
  compiler_outcome: CompilationOutcome
  compiler_diagnostics: CompilationDiagnostic[]
  compiler_dependencies: DefinitionDependency[]
  compiler_version: string
  catalogue_definition_revision: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export type SavedQueryDetail = SavedQuery & { revisions: SavedQueryRevision[] }
export type SavedQueryList = { items: SavedQuery[]; total: number }

export type CatalogueMaterializationRecord = {
  id: string
  name: string
  qualified_name: string
  display_name: string
  description: string | null
  view_reference_id: string
  view_uuid: string
  view_name: string
  source_table: string
  source_table_id: number
  source_table_uuid: string
  control_snapshot: number
  desired_state: "live" | "paused" | "deleting"
  observed_state: MaterializationObservedState
  nats_consumer_name: string
  refresh_delay_seconds: number
  refresh_strategy: "keyed" | "append" | "full"
  key_columns: string[]
  partition_column: string | null
  target_table_id: number | null
  ducklake_table_uuid: string | null
  bootstrap_snapshot: number | null
  bootstrap_partition_count: number | null
  bootstrap_partition_cursor: number | null
  processed_snapshot: number | null
  last_refreshed_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}
export type CatalogueMaterializationList = {
  items: CatalogueMaterializationRecord[]
  total: number
}
