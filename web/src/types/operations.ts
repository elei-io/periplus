export type TaskStateMetrics = {
  primitive: string
  queued: number
  running: number
  succeeded: number
  failed: number
  cancelled: number
}

export type PermitMetrics = {
  scope: "browser" | "policy"
  policy: string
  match: string | null
  in_use: number
  capacity: number
}

export type OperationsMetricsResponse = {
  generated_at: string
  window_seconds: number
  cluster: {
    workers_live: number
    workers_stale: number
    worker_capacity: number
    worker_active_runs: number
    oldest_live_worker_heartbeat_age_seconds: number
    oldest_queued_age_seconds: number
    tasks: TaskStateMetrics[]
    permits: PermitMetrics[]
  }
  tasks: {
    terminal_runs: number
    succeeded: number
    failed: number
    cancelled: number
    success_ratio: number
    queue_p50_seconds: number
    queue_p95_seconds: number
    execution_p50_seconds: number
    execution_p95_seconds: number
  }
  crawls: {
    total: number
    succeeded: number
    failed: number
    success_ratio: number
    duration_p50_seconds: number
    duration_p95_seconds: number
  }
  domains: Array<{
    domain: string
    total: number
    failed: number
    success_ratio: number
    duration_p95_seconds: number
  }>
  failures: Array<{
    reason: string
    count: number
  }>
  prometheus: {
    configured: boolean
    available: boolean
    capacity_waiters: number | null
    capacity_wait_p95_seconds: number | null
    page_acquisitions_per_second: number | null
    page_success_ratio: number | null
    dropped_observations: number | null
    error: string | null
  }
}
