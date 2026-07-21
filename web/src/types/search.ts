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
  row_count: number | null
  truncated: boolean | null
  result: string | null
  delta: string | null
  summary: string | null
  acquisition_plans: AcquisitionPlan[] | null
  schedule_changes: ScheduleChangeProposal[] | null
  search_results: SeedSearchResult[] | null
  chat_item_id: string | null
}

export type SeedSearchResult = {
  title: string
  url: string
  description: string | null
}

export type AcquisitionPlan = {
  graph_id: string
  graph_slug: string
  name: string
  purpose: string
  mode: "reconnaissance" | "corpus" | "monitoring"
  start_urls: string[]
  max_crawls: number
  recommended_run_type: "one_off" | "scheduled"
  schedule_summary: string | null
  expected_coverage: string
  success_criteria: string
}

export type ScheduleChangeProposal = {
  action: "update" | "pause" | "resume" | "delete"
  schedule_id: string
  graph_id: string
  schedule_name: string
  reason: string
  replacement: Record<string, unknown> | null
}

export type SearchQueryTrace = {
  callId: string
  queryId: string | null
  sql: string
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  status: "running" | "completed" | "failed"
  error: string | null
}

export type ChatToolArtifact = {
  call_id: string
  tool: string
  arguments: Record<string, unknown>
  result_preview: string | null
  search_results: SeedSearchResult[]
  status: "completed" | "failed"
  error: string | null
}

export type ChatQueryArtifact = {
  call_id: string
  query_id: string | null
  sql: string
  columns: string[]
  column_types: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  status: "completed" | "failed"
  error: string | null
}

export type UserMessageContent = {
  kind: "user_message"
  text: string
}

export type AssistantTurnContent = {
  kind: "assistant_turn"
  summary: string
  tools: ChatToolArtifact[]
  queries: ChatQueryArtifact[]
  acquisition_plans: AcquisitionPlan[]
  schedule_changes: ScheduleChangeProposal[]
  failed: boolean
}

export type UserActionContent = {
  kind: "user_action"
  action:
    | "graph_run_started"
    | "schedule_created"
    | "schedule_paused"
    | "schedule_resumed"
    | "schedule_deleted"
    | "schedule_updated"
  related_item_id: string
  graph_id: string
  graph_run_id: string | null
  schedule_id: string | null
  label: string
}

export type ChatItem = {
  id: string
  sequence: number
  content: UserMessageContent | AssistantTurnContent | UserActionContent
  created_at: string
}

export type Chat = {
  id: string
  title: string
  created_at: string
  updated_at: string
  items: ChatItem[]
}

export type ChatSummary = {
  id: string
  title: string
  preview: string | null
  item_count: number
  created_at: string
  updated_at: string
}

export type ChatList = {
  items: ChatSummary[]
  total: number
}

export type ChatGraphRunSubmission = {
  graph_id: string
  run_id: string
  status: string
  action_item: ChatItem
}
