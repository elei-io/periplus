export type QueryMode = "stable" | "experimental"

export type PreparedQuery = {
  query_mode: QueryMode
  compiler_version: string
  optimizations: string[]
  schema_version: "public_v1"
  query_id: string
  sql: string
  parameters: unknown[]
  diagnostics: { severity: string; code: string; message: string }[]
  plan: string
}

export type QueryResult = PreparedQuery & {
  source_snapshot: number
  columns: string[]
  types: string[]
  rows: unknown[][]
  elapsed_ms: number
  truncated: boolean
}
