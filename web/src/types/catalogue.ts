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
  managed: boolean
  available: boolean
  created_at: string | null
  updated_at: string | null
  created_from_query_revision_id: string | null
  attached_materialized_views: Array<{
    id: string
    name: string
    display_name: string
    refresh_mode: "full" | "scope_incremental"
  }>
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

export type MaterializedViewRecord = {
  id: string
  name: string
  qualified_name: string
  display_name: string
  description: string | null
  query_revision_id: string | null
  query_id: string | null
  query_name: string | null
  query_revision: number | null
  source_view_uuid: string | null
  source_view_name: string | null
  refresh_mode: "full" | "scope_incremental"
  scope_kind: "document" | null
  activation_snapshot: number | null
  live_enabled: boolean
  backfill_enabled: boolean
  backfill_scopes_per_minute: number
  partition_column: string | null
  partitioning: string[]
  status: "full_refresh" | "live" | "backfilling" | "paused" | "deleting" | "degraded"
  completed_scopes: number | null
  total_scopes: number | null
  failed_scopes: number
  last_scope_completed_at: string | null
  active_file_count: number
  active_storage_bytes: number
  deletion_requested_at: string | null
  ducklake_table_uuid: string
  row_count: number
  columns: Array<{ name: string; data_type: string; nullable: boolean }>
  last_refreshed_at: string
  created_at: string
  updated_at: string
}
export type MaterializedViewList = { items: MaterializedViewRecord[]; total: number }
