export type CoverageRequestStatus = "pending" | "resolving" | "ongoing" | "completed" | "failed"
export type CoverageRequestInput = {
  kind: "url" | "description"
  input: string
  depth: number
  link_scope: "internal" | "external" | "both"
  max_pages: number
}
export type CoverageRequest = CoverageRequestInput & {
  id: string
  status: CoverageRequestStatus
  created_at: string
  completed_at: string | null
  run_id: string | null
  resolved_urls: string[]
  search_queries: string[]
  error: string | null
  retry_at: string | null
  progress: { status: string; request_count: number; pending_request_count: number; failed_request_count: number; crawl_limit_reached: boolean } | null
}
export type CoverageRequestPage = {
  items: CoverageRequest[]
  total: number
  limit: number
  offset: number
}
