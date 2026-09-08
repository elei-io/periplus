import type { StartEstimate } from "@/types/frontier-items"
export interface RecentCapture {
  observation_id: string
  requested_url: string
  completed_at: string
  evidence_committed: boolean
  query_ready: boolean | null
  query_readiness_reason: string
}
export interface CrawlerActivity {
  as_of: string
  state: "observed" | "no_recent_reports" | "unavailable"
  reason: string | null
  reported_workers: number | null
  ready_workers: number | null
  blocked_workers: number | null
  checking_workers: number | null
  unknown_workers: number | null
  waiting_reasons: Array<"ingestion_delivery_unavailable" | "storage_unavailable" | "cdp_unavailable">
  excluded_reports: number | null
  more_workers: boolean
}
export interface LiveView {
  workers: CrawlerActivity
  current: {
    as_of: string
    paused: boolean
    queued: number
    dispatched: number
    started: number
    oldest_wait_at: string | null
    domains: Array<{domain: string; unique_queued_urls: number; request_queued_urls: number | null; queued: number; dispatched: number; started: number; oldest_wait_at: string | null}>
    more_domains: boolean
    upcoming: Array<{acquisition_id: string; requested_url: string; domain: string; admitted_at: string; retry_not_before: string}>
    active: Array<{acquisition_id: string; requested_url: string; attempt_started_at: string | null}>
    more_active: boolean
    upcoming_semantics: "oldest_pending_preview_not_dispatch_order"
    next_start_estimate: StartEstimate | null
    estimate_unavailable_reason: string | null
    recent: RecentCapture[]
  }
  history: {
    as_of: string
    window_end: string
    completeness: "committed_evidence_only_ingestion_may_lag"
    domain_preview_limit: 10
    velocities: Array<{seconds: 60 | 300; domain: string | null; attempt_starts: number; successful_captures: number; failed_captures: number; fulfillments: number; attempt_starts_per_minute: number}>
    recent: RecentCapture[]
  } | null
  history_unavailable_reason: string | null
  recent: RecentCapture[]
}

export interface CapturePage {
  items: RecentCapture[]
  cursor: string
  has_more: boolean
  bootstrap: boolean
  reset_reason: "cursor_expired" | "clock_moved_backwards" | null
  as_of: string
  source: "retained_public_completions"
}

export interface CaptureBatch extends CapturePage {
  tail: RecentCapture[]
}
