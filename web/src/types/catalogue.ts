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
}

export type CatalogueViewList = { items: CatalogueViewRecord[] }
