export type IngestionReport = {
  status: "current" | "processing" | "attention"
  generated_at: string
  archive_heads: number[]
  archive_events: number
  queue: { pending: number; ack_pending: number; redelivered: number }
  targets: { id: string; phase: string; source_lag: number | null; failed_batches: number; running_batches: number; blocker: string | null }[]
}
