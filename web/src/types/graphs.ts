export type CrawlGraphNode = {
  id: string
  graph_id: string
  name: string
  description: string | null
  position_x: number | null
  position_y: number | null
  created_at: string
}

export type EdgeDedupeMode = "graph" | "crawl" | "document"

export type CrawlGraphEdge = {
  id: string
  graph_id: string
  source_node_id: string
  target_node_id: string
  name: string
  description: string | null
  sql: string
  dedupe_mode: EdgeDedupeMode
  created_at: string
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
  root_urls: string[]
  overlap_policy: "skip" | "allow"
  misfire_policy: "skip" | "run_once"
}

export type CrawlSchedule = CrawlScheduleInput & {
  id: string
  graph_id: string
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
  graph_slug: string
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
  graph_id: string
  run_id: string
  status: "queued"
}

export type GraphRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled"

export type GraphRunRecord = {
  id: string
  graph_id: string
  graph_slug?: string | null
  status: GraphRunStatus
  trigger_kind: "manual" | "schedule"
  trigger_schedule_id: string | null
  trigger_urls: string[]
  request_count: number
  pending_request_count: number
  failed_request_count: number
  error_count: number
  queued_request_count?: number
  fetching_request_count?: number
  processing_request_count?: number
  created_at: string
  started_at: string | null
  last_progress_at: string | null
  completed_at: string | null
  cancel_requested_at: string | null
  error: string | null
}

export type GraphRunListResponse = {
  items: GraphRunRecord[]
  total: number
}

export type GraphRunFailure = {
  crawl_id: string
  requested_url: string
  final_url: string | null
  status_code: number | null
  failure_code: string | null
  failure_stage: string | null
  failure_detail: string | null
  captured_at: string
}

export type GraphRunFailureList = {
  items: GraphRunFailure[]
  total: number
}

export type CrawlConcurrencyLimits = {
  worker_count: number
  runtime_capacity: number
  runtime_active: number
  resource_acquire_timeout_seconds: number
  resources: Array<{
    name: string
    capacity: number
    used: number
    critical: number
    live: number
    backfill: number
    maintenance: number
    waiting: number
    critical_waiting: number
    live_waiting: number
    backfill_waiting: number
    maintenance_waiting: number
    oldest_wait_seconds: number
  }>
  workers: Array<{
    worker_id: string
    capacity: number
    active_request_count: number
    last_seen_at: string
  }>
  catalogue_executors: Array<{
    capability: "ingestion" | "materialization"
    worker_count: number
    capacity: number
    active: number
  }>
  tuning: {
    catalogue_max_concurrency: number
    effective_catalogue_concurrency: number
    object_io_max_concurrency: number
    crawl_lanes_per_replica: number
    catalogue_lanes_per_replica: number
    graph_consumer_delivery_ceiling: number
    duckdb_threads_per_executor: number
    duckdb_memory_limit_per_executor: string
    catalogue_read_pool_size: number
  }
}

export type GraphRunMaterializationLag = {
  run_id: string
  materialization_count: number
  pending_updates: number
  failed_updates: number
}

export type GraphRunMaterializationLagList = {
  items: GraphRunMaterializationLag[]
}
