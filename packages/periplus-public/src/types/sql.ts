export interface SqlQueryResult {
  columns: string[]
  types: string[]
  rows: unknown[][]
  truncated: boolean
}

export function isSqlQueryResult(value: unknown): value is SqlQueryResult {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<SqlQueryResult>
  return (
    Array.isArray(candidate.columns) &&
    candidate.columns.every((column) => typeof column === "string") &&
    Array.isArray(candidate.types) &&
    candidate.types.every((type) => typeof type === "string") &&
    Array.isArray(candidate.rows) &&
    candidate.rows.every((row) => Array.isArray(row)) &&
    typeof candidate.truncated === "boolean"
  )
}
