export type CrawlGraphNode = {
  id: string
  graph_id: string
  name: string
  description: string | null
  position_x: number | null
  position_y: number | null
  created_at: string
}

export type CrawlGraphEdge = {
  id: string
  graph_id: string
  source_node_id: string
  target_node_id: string
  name: string
  description: string | null
  sql: string
  created_at: string
}

export type CrawlGraphEdgeInput = {
  source_node_id: string
  target_node_id: string
  name: string
  description: string
  sql: string
}

export type CrawlGraphSummary = {
  id: string
  slug: string
  description: string | null
  root_node_id: string | null
  system_owned: boolean
  created_at: string
}

export type CrawlGraphDetail = {
  id: string
  slug: string
  description: string | null
  root_node_id: string | null
  system_owned: boolean
  created_at: string
  nodes: CrawlGraphNode[]
  edges: CrawlGraphEdge[]
}

export type CrawlGraphUpdateInput = {
  slug: string
  description: string
  root_node_id: string | null
}

export type CrawlGraphListResponse = {
  items: CrawlGraphSummary[]
  total: number
}

export type ScheduleTiming =
  | { kind: "interval"; seconds: number }
  | { kind: "cron"; expression: string; timezone: string }

export type CrawlScheduleInput = {
  name: string
  enabled: boolean
  timing: ScheduleTiming
  starts_at: string | null
  ends_at: string | null
  maximum_run_count: number | null
  max_crawls: number
  urls: string[]
  overlap_policy: "skip" | "allow"
  misfire_policy: "skip" | "run_once"
}

export type CrawlSchedule = CrawlScheduleInput & {
  id: string
  plan_id: string
  status: "active" | "paused" | "not_started" | "exhausted" | "ended"
  run_count: number
  next_run_at: string | null
  last_occurrence_at: string | null
  last_run_id: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export type CrawlScheduleResource = CrawlSchedule & {
  plan_slug: string
}

export type CrawlScheduleListResponse = {
  items: CrawlSchedule[]
  total: number
}

export type CrawlScheduleResourceListResponse = {
  items: CrawlScheduleResource[]
  total: number
}

export type SchedulePreviewResponse = {
  occurrences: string[]
}

export type GraphRunSubmission = {
  plan_id: string
  run_id: string
  status: "queued"
}

export type GraphRunTrigger = {
  urls: string[]
  max_crawls: number
  max_run_seconds?: number
}

export type GraphRunStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled"

export type GraphRunRecord = {
  id: string
  plan_id: string
  plan_slug?: string | null
  status: GraphRunStatus
  trigger_kind: "manual" | "schedule"
  trigger_schedule_id: string | null
  trigger_urls: string[]
  max_crawls: number
  crawl_limit_reached: boolean
  request_count: number
  pending_request_count: number
  failed_request_count: number
  error_count: number
  queued_request_count: number
  fetching_request_count: number
  navigating_request_count: number
  created_at: string
  started_at: string | null
  last_progress_at: string | null
  completed_at: string | null
  paused_at: string | null
  not_before: string | null
  deadline_at: string | null
  cancel_requested_at: string | null
  error: string | null
}

export type GraphRunDetail = {
  id: string
  graph_id: string
  status: GraphRunStatus
  trigger_kind: "manual" | "schedule"
  trigger_schedule_id: string | null
  trigger_urls: string[]
  max_crawls: number
  crawl_limit_reached: boolean
  request_count: number
  pending_request_count: number
  acquisition_pending_count: number
  failed_request_count: number
  error_count: number
  created_at: string
  started_at: string | null
  last_progress_at: string | null
  completed_at: string | null
  paused_at: string | null
  not_before: string | null
  deadline_at: string | null
  cancel_requested_at: string | null
  error: string | null
}

export type GraphRunListResponse = {
  items: GraphRunRecord[]
  total: number
}

export type GraphRunFailureGroup = {
  failure_stage: string
  failure_code: string
  status_code: number | null
  count: number
  example_url: string
  example_detail: string | null
  last_occurred_at: string
}

export type GraphRunFailureSummary = {
  items: GraphRunFailureGroup[]
  total: number
}

export type CrawlConcurrencyLimits = {
  worker_count: number
  runtime_capacity: number
  runtime_active: number
  workers: Array<{
    worker_id: string
    capacity: number
    active_request_count: number
    last_seen_at: string
  }>
}
