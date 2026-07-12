export type TaskPrimitive =
  "search" | "index" | "crawl" | "schema" | "extract" | "calibrate"

export type TaskRunSubmission = {
  task_id: string
  run_id: string
  status: "queued"
}

export type TaskRunStatus =
  "queued" | "running" | "succeeded" | "failed" | "cancelled" | "skipped"

export type TaskRunRecord = {
  id: string
  task_id: string
  task_revision: number
  primitive: TaskPrimitive
  status: TaskRunStatus
  trigger_kind: "scheduled" | "manual" | "retry" | "backfill"
  queued_at: string
  started_at: string | null
  finished_at: string | null
  cancellation_requested_at: string | null
  cancelled_at: string | null
  retry_at: string | null
  attempt: number
  failed_attempts: number
  max_attempts: number
  input_json: Record<string, unknown>
  warnings_json: Record<string, unknown>
  error: string | null
  created_at: string
  updated_at: string
}

export type TaskProgressEnvelope<T = unknown> = {
  event_id: string
  run_id: string
  attempt: number
  sequence: number
  type: "progress" | "succeeded" | "failed" | "cancelled" | "skipped"
  timestamp: string
  data: T
}
