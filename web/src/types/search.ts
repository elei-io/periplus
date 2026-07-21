export type SearchEventType =
  | "run.started"
  | "agent.status"
  | "tool.started"
  | "tool.completed"
  | "query.started"
  | "query.completed"
  | "query.failed"
  | "search.completed"
  | "summary.delta"
  | "run.completed"
  | "run.failed"

export type SearchEvent = {
  type: SearchEventType
  run_id: string
  call_id: string | null
  tool: string | null
  message: string | null
  arguments: Record<string, unknown> | null
  sql: string | null
  query_id: string | null
  columns: string[] | null
  column_types: string[] | null
  rows: unknown[][] | null
  delta: string | null
  summary: string | null
  acquisition_plan: AcquisitionPlan | null
  search_results: SeedSearchResult[] | null
}

export type SeedSearchResult = {
  title: string
  url: string
  description: string | null
}

export type AcquisitionPlan = {
  graph_id: string
  graph_slug: string
  start_urls: string[]
  recommended_run_type: "one_off" | "scheduled"
  schedule_summary: string | null
}

export type SearchQueryTrace = {
  callId: string
  queryId: string | null
  sql: string
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
  status: "running" | "completed" | "failed"
  error: string | null
}
