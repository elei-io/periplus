export type DataStatusState =
  "current" | "processing" | "attention" | "unavailable"

export type MaterializationRun = {
  id: string
  status:
    | "queued"
    | "planning"
    | "running"
    | "activating"
    | "completed"
    | "failed"
  source_snapshot: number
  covered_snapshot: number
  activation_snapshot: number | null
  registry_digest: string
  batch_size: number
  total_batches: number
  completed_batches: number
  source_items: number
  source_bytes: number
  output_rows: number
  output_bytes: number
  progress: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export type SourceRecordLag = {
  available: boolean
  unit: "source_records"
  value: number | null
  reason: string | null
}

export type DeliveryQueue = {
  available: boolean
  unit: "ingestion_jobs" | "materialization_batches"
  pending: number | null
  ack_pending: number | null
  redelivered: number | null
  waiting_for_redelivery: number | null
  total: number | null
}

export type WorkerCapacity = {
  worker_count: number
  configured_capacity: number
  usable_capacity: number
  active_operations: number
  degraded_capacity: number
}

export type MaterializationWorkload = {
  name: "visits"
  source: "ingest.visits"
  projections: string[]
  queue: DeliveryQueue
}

export type MaterializationRunSummary = {
  id: string
  status:
    | "queued"
    | "planning"
    | "running"
    | "activating"
    | "completed"
    | "failed"
  total_batches: number
  completed_batches: number
  source_items: number
  source_bytes: number
  output_rows: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export type DataStatus = {
  status: DataStatusState
  generated_at: string
  source_record_lag: SourceRecordLag
  ingestion: {
    status: DataStatusState
    queue: DeliveryQueue
    workers: WorkerCapacity
    dead_letters: number
  }
  materialization: {
    status: DataStatusState
    source_record_lag: SourceRecordLag
    workloads: MaterializationWorkload[]
    workers: WorkerCapacity
  }
  materialization_runs: MaterializationRunSummary[]
}
