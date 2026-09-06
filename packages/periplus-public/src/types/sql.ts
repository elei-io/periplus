export type PreparedQuery = {
  query_id: string
  sql: string
  parameters: unknown[]
  diagnostics: { severity: string; code: string; message: string }[]
  plan: string
}

export type QueryResult = PreparedQuery & {
  columns: string[]
  types: string[]
  rows: unknown[][]
  elapsed_ms: number
  truncated: boolean
}
