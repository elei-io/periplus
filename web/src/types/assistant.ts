export type AssistantActivityKind = "catalogue" | "query" | "draft"

export type AssistantEventType =
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "response.completed"
  | "response.failed"

export type AssistantMessage = {
  role: "user" | "assistant"
  content: string
}

export type AssistantAnswer = {
  markdown: string
}

export type AssistantSqlSuggestion = {
  title: string
  description: string
  sql: string
  display_sql: string
}

export type AssistantEvent = {
  type: AssistantEventType
  run_id: string
  call_id: string | null
  tool: string | null
  activity: AssistantActivityKind | null
  purpose: string | null
  sql: string | null
  display_sql: string | null
  row_count: number | null
  truncated: boolean | null
  columns: string[] | null
  types: string[] | null
  rows: unknown[][] | null
  duration_ms: number | null
  message: string | null
  response: AssistantAnswer | null
  suggestions: AssistantSqlSuggestion[]
}

export type AssistantActivity = {
  callId: string
  kind: AssistantActivityKind
  label: string
  state: "running" | "completed" | "failed"
  durationMilliseconds: number | null
  message: string | null
}

export type AssistantQueryResult = {
  callId: string
  purpose: string
  sql: string
  displaySql: string
  state: "running" | "completed" | "failed"
  durationMilliseconds: number | null
  rowCount: number | null
  truncated: boolean
  columns: string[]
  types: string[]
  rows: unknown[][]
  message: string | null
}

export type AssistantTurn = {
  id: string
  prompt: string
  status: "running" | "completed" | "failed"
  answer: AssistantAnswer | null
  suggestions: AssistantSqlSuggestion[]
  activities: AssistantActivity[]
  queries: AssistantQueryResult[]
  error: string | null
}

export type AssistantSqlResult = {
  columns: string[]
  types: string[]
  rows: unknown[][]
  truncated: boolean
}
