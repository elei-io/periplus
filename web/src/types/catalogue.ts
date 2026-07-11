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
  query_revision_id: string
  query_id: string
  query_name: string
  query_revision: number
  ducklake_table_uuid: string
  row_count: number
  columns: Array<{ name: string; data_type: string; nullable: boolean }>
  last_refreshed_at: string
  created_at: string
  updated_at: string
}
export type MaterializedViewList = { items: MaterializedViewRecord[]; total: number }
