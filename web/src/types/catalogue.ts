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

export type CatalogueQueryRuntime = {
  transport: "quack"
  quack_uri: string
  quack_token: string
  catalogue_alias: string
  catalogue_schema: string
  metadata_schema: string
  catalogue_schema_version: string
  setup_sql: string[]
  attach_sql: string
}

export type CataloguePreparedSql = {
  sql: string
  statement_kind: CatalogueStatementKind
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
  status:
    "live" | "backfilling" | "paused" | "dematerializing" | "source_changed"
  definition_is_current: boolean
}

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
  scope_kind: "document" | "crawl"
  scope_column: string
  activation_snapshot: number
  live_enabled: boolean
  backfill_enabled: boolean
  backfill_scopes_per_minute: number
  partition_column: string | null
  definition_revision_id: string
  status:
    "live" | "backfilling" | "paused" | "dematerializing" | "source_changed"
  source_state: "current" | "source_changed"
  dematerialization_requested_at: string | null
  ducklake_table_uuid: string
  last_refreshed_at: string
  created_at: string
  updated_at: string
}
export type CatalogueMaterializationList = {
  items: CatalogueMaterializationRecord[]
  total: number
}
