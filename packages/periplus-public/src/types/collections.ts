import type { StartEstimate } from "@/types/frontier-items"

export interface CollectionSpec {
  seed_urls: string[]
  seed_description: string | null
  seed_sql: string | null
  seed_parameters: unknown[]
  follow_sql: string
  follow_link_limit: number
  max_depth: number
  page_limit: number
  result_max_age_seconds: number
  retention_seconds: number | null
  request_class: "public" | "system" | "admin"
  allowed_sections: string[]
  max_duration_seconds: number | null
}

interface CollectionBase {
  id: string
  specification: CollectionSpec
  created_at: string
  completed_at: string | null
  expires_at: string | null
  retention_expired: boolean
  outcome: string | null
  consumed_pages: number | null
  supplied_pages: number | null
  failed_pages: number | null
  query_ready: boolean | null
  query_readiness_reason: string
  query_readiness_as_of: string | null
  query_generation_id: string | null
  as_of: string
}

export interface AdmissionEstimate extends Omit<StartEstimate, "basis"> {
  basis: "recent_single_url_submission_to_admission_waits"
  scope: "first_admission"
}

export interface AdmissionWait {
  estimate: AdmissionEstimate | null
  pending_candidates: number
  preview_urls: string[]
  oldest_selected_at: string | null
  elapsed_seconds: number | null
  estimate_unavailable_reason: string | null
}

export interface CollectionQueue {
  runnable_pages: number
  deferred_pages: number
  unknown_pages: number
  oldest_admitted_at: string | null
  oldest_wait_seconds: number | null
  constraints: { reason: string; pages: number }[]
  basis: "stored_eligibility_permits_rechecked_at_start"
}

export interface CurrentCollection extends CollectionBase {
  queue: CollectionQueue
  last_progress_at: string | null
  admission: AdmissionWait
  source: "current"
  status: "active" | "paused" | "settled"
  priority: number
  reserved_pages: number
  consumed_pages: number
  supplied_pages: number
  failed_pages: number
  seeds_settled: boolean
  waiting_reason: string | null
  queued_pages: number
  acquiring_pages: number
  selecting_pages: number
  shared_pages: number
  reused_pages: number
  ingested_pages: number
  lineage_ready: boolean
  discovery_stage: string | null
  search_queries: string[]
  resolved_urls: string[]
}

export interface HistoricalCollection extends CollectionBase {
  source: "history"
  seed_provenance: Record<string, unknown> | null
}
export type Collection = CurrentCollection | HistoricalCollection
export interface CollectionPage {
  source: "current"
  items: CurrentCollection[]
  limit: number
  offset: number
}
export interface CollectionHistoryPage {
  source: "history"
  items: Array<
    Pick<
      CollectionBase,
      | "id"
      | "created_at"
      | "completed_at"
      | "outcome"
      | "consumed_pages"
      | "supplied_pages"
      | "failed_pages"
    > & {
      request_class: "public" | "system" | "admin"
      summary: string
    }
  >
  next_cursor: string | null
  as_of: string
}
export type CollectionChange =
  { action: "pause" | "resume" | "cancel" } | { priority: number }

export interface CreateCollection {
  id: string
  specification: CollectionSpec
  priority: number
}
