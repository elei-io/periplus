export type ArtifactKind =
  | "html"
  | "screenshot"
  | "pdf"
  | "mhtml"

export type ArtifactRecord = {
  id: string
  crawl_id: string | null
  url_id: string | null
  task_run_id: string | null
  kind: ArtifactKind
  path: string
  content_type: string
  size_bytes: number
  sha256: string
  input_hash: string | null
  warning_count: number
  invalidated_at: string | null
  invalidated_reason: string | null
  created_at: string
  url: string | null
  normalized_url: string | null
  domain: string | null
  path_name: string | null
}

export type PageParams = {
  limit: number
  offset: number
}

export type PaginatedResponse<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type ArtifactListResponse = PaginatedResponse<ArtifactRecord>

export type ArtifactDetailRecord = ArtifactRecord & {
  meta: Record<string, unknown>
  warnings_json: Record<string, unknown>
}

export type ArtifactFilters = {
  urlPattern: string
  kind: "all" | ArtifactKind
  invalidated: "all" | "active" | "invalidated"
  warnings: "all" | "clean" | "warning"
}

export type ArtifactInvalidateRequest = {
  artifact_ids?: string[]
  url_ids?: string[]
  url_pattern?: string
  kind?: ArtifactKind
  warnings?: boolean
  reason: string
}

export type ArtifactInvalidateResponse = {
  invalidated: number
}

export type UrlRecord = {
  id: string
  url: string
  normalized_url: string
  scheme: string
  host: string
  domain: string
  path: string
  query_fingerprint: string | null
  crawl_count: number
  artifact_count: number
  latest_status_code: number | null
  latest_crawl_at: string | null
  warning_count: number
}

export type UrlListResponse = PaginatedResponse<UrlRecord>

export type UrlDetailRecord = UrlRecord & {
  recent_crawls: CrawlRecord[]
  artifacts: ArtifactRecord[]
}

export type UrlFilters = {
  urlPattern: string
  domain: string
}

export type CrawlRecord = {
  id: string
  url_id: string
  task_run_id: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  input_hash: string
  success: boolean
  status_code: number | null
  retry_count: number
  warning_count: number
  error_message: string | null
  artifact_count: number
  url: string
  normalized_url: string
  domain: string
  path_name: string
}

export type CrawlListResponse = PaginatedResponse<CrawlRecord>

export type CrawlDetailRecord = CrawlRecord & {
  inputs_json: Record<string, unknown>
  redirects_json: Record<string, unknown>
  errors_json: Record<string, unknown>
  warnings_json: Record<string, unknown>
  meta: Record<string, unknown>
  created_at: string
  artifacts: ArtifactRecord[]
}

export type CrawlFilters = {
  urlPattern: string
  domain: string
  success: "all" | "succeeded" | "failed"
  statusCode: string
  warnings: "all" | "clean" | "warning"
}
