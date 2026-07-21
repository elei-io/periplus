export type CatalogueStatementKind = "query" | "explain" | "explain_analyze"

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
}

export type CatalogueLintResult = {
  diagnostics: CatalogueLintDiagnostic[]
}

export type CatalogueMaterializationSummary = {
  id: string
  status: MaterializationObservedState
}

export type MaterializationObservedState =
  | "creating"
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
