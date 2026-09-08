export type QueryStats = {
  executions: number
  successes: number
  failures: number
  truncated: number
  p50_ms: number | null
  p95_ms: number | null
  total_ms: number
}
export type QueryPattern = QueryStats & {
  pattern_key: string
  query_template: string | null
  last_seen: string
  sources: string[]
}
export type QueryBucket = QueryStats & { day: string }
export type QueryCount = { name: string; count: number }
export type QueryDashboard = {
  summary: QueryStats
  trend: QueryBucket[]
  patterns: QueryPattern[]
  plans: QueryPlanVariant[]
  pattern_count: number
  failures: QueryCount[]
  relations: QueryCount[]
  functions: QueryCount[]
}
export type QueryExecutionSummary = {
  execution_id: string
  started_at: string
  source: string
  outcome: string
  error_code: string | null
  elapsed_ms: number
  result_rows: number | null
  truncated: boolean | null
}
export type QueryExecutionPage = {
  executions: QueryExecutionSummary[]
  has_more: boolean
}
export type QueryExecution = QueryExecutionSummary & {
  request_id: string | null
  finished_at: string
  operation: string
  sql_text: string
  parameters: unknown[]
  query_template: string | null
  query_fingerprint: string | null
  fingerprint_version: string
  relations: string[]
  functions: string[]
  features: Record<string, number>
  result_bytes: number | null
  source_snapshot: number | null
  service_version: string | null
  plan: string | null
  plan_truncated: boolean | null
  plan_fingerprint: string | null
  diagnostics: { severity: string; code: string; message: string }[] | null
  duckdb_version: string | null
  compiler_version: string | null
  effective_limits: Record<string, number> | null
}

export type QueryPlanVariant = QueryStats & {
  plan_fingerprint: string | null
  first_seen: string
  last_seen: string
  example_execution_id: string
  duckdb_version: string | null
  compiler_version: string | null
  timeouts: number
}
