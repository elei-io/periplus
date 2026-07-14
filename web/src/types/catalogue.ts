export type CatalogueQueryMode = "run" | "explain" | "explain_analyze"

export type CatalogueQueryRequest = {
  sql: string
  mode: CatalogueQueryMode
}

export type CatalogueQueryResult = {
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
}

export type CatalogueLintDiagnostic = {
  code: string
  severity: "warning"
  message: string
}

export type CatalogueLintResult = {
  diagnostics: CatalogueLintDiagnostic[]
}

export type CatalogueMaterializationSummary = {
  id: string
  status: "live" | "backfilling" | "paused" | "dematerializing" | "degraded" | "source_changed"
  row_count: number
  storage_bytes: number
  definition_is_current: boolean
  pending_live_scopes: number
  remaining_backfill_scopes: number
  failed_scopes: number
  last_scope_completed_at: string | null
}

export type CatalogueViewRecord = {
  id: string | null
  ducklake_view_uuid: string
  schema_name: string
  view_name: string
  qualified_name: string
  display_name: string
  description: string | null
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
  name: string
  description: string | null
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
  partitioning: string[]
  status: "live" | "backfilling" | "paused" | "dematerializing" | "degraded" | "source_changed"
  source_state: "current" | "source_changed"
  completed_scopes: number | null
  total_scopes: number | null
  pending_live_scopes: number
  remaining_backfill_scopes: number
  failed_scopes: number
  last_scope_completed_at: string | null
  active_file_count: number
  active_storage_bytes: number
  dematerialization_requested_at: string | null
  ducklake_table_uuid: string
  row_count: number
  columns: Array<{ name: string; data_type: string; nullable: boolean }>
  last_refreshed_at: string
  created_at: string
  updated_at: string
}
export type CatalogueMaterializationList = { items: CatalogueMaterializationRecord[]; total: number }
