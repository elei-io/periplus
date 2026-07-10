export type ProgressStatus = "waiting" | "started" | "succeeded" | "failed"

export type ProgressPhase =
  | "apply_data_schema"
  | "cache"
  | "calibrate"
  | "calibration_candidate"
  | "collect_query_evidence"
  | "crawl"
  | "crawl_batch"
  | "crawl_capacity"
  | "extract"
  | "extract_query_params"
  | "generate_schema"
  | "index_depth"
  | "persist_result"
  | "regenerate_data_schema"
  | "schema"
  | "search_provider"
  | "task"
  | "queue"

export type ProgressEvent = {
  phase: ProgressPhase
  status: ProgressStatus
  resource?: string
  current?: number
  total?: number
  message?: string
  metadata?: Record<string, unknown>
  duration?: number
  error?: string
  operation_id: string
}
