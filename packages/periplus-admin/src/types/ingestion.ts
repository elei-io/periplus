import type {
  DataStatusState,
  DeliveryQueue,
  WorkerCapacity,
} from "@/types/operations"

export type IngestionWorker = {
  worker_id: string
  started_at: string
  last_seen_at: string
  process_ready: boolean
  lanes: {
    lane_index: number
    status: "starting" | "available" | "active" | "unavailable"
    active: boolean
  }[]
}

export type IngestionReport = {
  status: DataStatusState
  generated_at: string
  queue: DeliveryQueue
  capacity: WorkerCapacity | null
  workers: IngestionWorker[] | null
  dead_letters: number | null
  recent_dead_letters: {
    items: {
      sequence: number
      request_id: string
      kind: "visit" | "lineage"
      failed_at: string
      enqueued_at: string
      processing_failure_count: number
      error: string
    }[]
    complete: boolean
    scanned_sequences: number
  } | null
  issues: string[]
}
