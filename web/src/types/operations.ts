export type DataStatusState =
  "current" | "processing" | "attention" | "unavailable"

export type MaterializationProjection =
  | "content_stats"
  | "html_elements"
  | "jsonld_values"
  | "links"
  | "link_occurrences"
  | "pages"
  | "page_observations"
  | "page_heads"

export type MaterializationRun = {
  id: string
  mode: "backfill" | "rebuild"
  status: "queued" | "running" | "completed" | "failed"
  requested_stages: MaterializationProjection[]
  stages: MaterializationProjection[]
  source_snapshot: number
  catchup_snapshot: number
  catchup_target_snapshot: number | null
  current_stage: number
  source_items: number
  source_bytes: number
  output_rows: number
  created_at: string
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
  unit: "ingestion_jobs" | "cdc_messages"
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
  name: "documents" | "visits"
  source: "ingest.documents" | "ingest.visits"
  projections: string[]
  queue: DeliveryQueue
}

export type MaintenanceRunSummary = {
  id: string
  mode: "backfill" | "rebuild"
  status: "queued" | "running" | "completed" | "failed"
  stages: string[]
  active_stage: string | null
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
  maintenance_runs: MaintenanceRunSummary[]
}
