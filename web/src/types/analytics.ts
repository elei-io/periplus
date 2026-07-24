export type DirectionId = string

export type AnalysisDirection = {
  id: DirectionId
  title: string
  objective: string
  rationale: string
}

export type AnalysisPlan = {
  category: string
  interpretation: string
  directions: AnalysisDirection[]
}

export type DirectionHandoffQuery = {
  title: string
  sql: string
  explanation: string
  caveats: string[]
}

export type AnalyticsEventType =
  | "analysis.started"
  | "orientation.activity"
  | "plan.completed"
  | "direction.started"
  | "direction.completed"
  | "direction.failed"
  | "handoff.started"
  | "handoff.completed"
  | "handoff.failed"
  | "query.started"
  | "query.completed"
  | "query.failed"
  | "summary.delta"
  | "analysis.completed"
  | "analysis.failed"

export type AnalyticsEvent = {
  type: AnalyticsEventType
  run_id: string
  plan: AnalysisPlan | null
  direction: AnalysisDirection | null
  direction_id: DirectionId | null
  direction_answer: string | null
  handoff_query: DirectionHandoffQuery | null
  scope: "orientation" | "analysis" | null
  call_id: string | null
  message: string | null
  sql: string | null
  query_id: string | null
  columns: string[] | null
  column_types: string[] | null
  rows: unknown[][] | null
  row_count: number | null
  truncated: boolean | null
  delta: string | null
  summary: string | null
}

export type AnalyticsQuery = {
  callId: string
  queryId: string | null
  sql: string
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
  rowCount: number
  truncated: boolean
  status: "running" | "completed" | "failed"
  error: string | null
}

export type DirectionState = {
  direction: AnalysisDirection
  answer: string
  error: string | null
  handoffError: string | null
  handoffQuery: DirectionHandoffQuery | null
  queries: AnalyticsQuery[]
  status: "waiting" | "running" | "compiling" | "completed" | "failed"
}

export type AnalyticsState = {
  error: string | null
  phase: "idle" | "planning" | "investigating" | "synthesizing" | "completed"
  plan: AnalysisPlan | null
  orientationActivities: string[]
  orientationQueries: AnalyticsQuery[]
  directions: DirectionState[]
  running: boolean
  summary: string
}
