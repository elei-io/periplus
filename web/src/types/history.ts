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

export type MetricCard = {
  metric: string
  label: string
  value: number | string
  unit: string | null
  tone: string | null
  description: string | null
}

export type MetricDatum = {
  metric: string
  label: string
  value: number
  labels: Record<string, string>
}

export type MetricBreakdown = {
  metric: string
  label: string
  unit: string | null
  items: MetricDatum[]
}

export type HistoryMetricsResponse = {
  window_seconds: number
  cards: MetricCard[]
  breakdowns: MetricBreakdown[]
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
  active_artifact_count: number
  invalidated_artifact_count: number
  cache_eligible_count: number
  latest_status_code: number | null
  latest_crawl_at: string | null
  latest_artifact_at: string | null
  warning_count: number
  crawl_warning_count: number
  artifact_warning_count: number
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

export type ExtractSchemaRecord = {
  id: string
  match: string
  enabled: boolean
  priority: number
  prompt: string
  prompt_hash: string
  schema_type: string
  target_json_hash: string | null
  domain: string | null
  path: string | null
  schema_hash: string
  validation_status: string | null
  failure_count: number
  last_failed_at: string | null
  last_error: string | null
  task_run_count: number
  warning_count: number
  created_at: string
  updated_at: string
}

export type ExtractSchemaListResponse = PaginatedResponse<ExtractSchemaRecord>
  & {
    summary: ExtractSchemaSummary
  }

export type ExtractSchemaSummary = {
  total_schemas: number
  enabled_schemas: number
  used_schemas: number
  total_schema_uses: number
  reused_schema_uses: number
  reuse_rate: number
  avg_uses_per_used_schema: number
  failing_schemas: number
}

export type ExtractSchemaDetailRecord = ExtractSchemaRecord & {
  identity_key: string
  schema_json: Record<string, unknown>
  generated_from_crawl_id: string | null
  generated_from_artifact_id: string | null
  generated_by_task_run_id: string | null
  inputs_json: Record<string, unknown>
  warnings_json: Record<string, unknown>
}

export type ExtractSchemaUpdateRequest = {
  match?: string
  enabled?: boolean
  priority?: number
  schema_json?: Record<string, unknown>
  validation_status?: string | null
}

export type ExtractSchemaFilters = {
  matchPattern: string
  prompt: string
  schemaType: "all" | "css" | "xpath"
  enabled: "all" | "enabled" | "disabled"
  warnings: "all" | "clean" | "warning"
}

export type PaginationSchemaRecord = {
  id: string
  identity_key: string
  match: string
  enabled: boolean
  priority: number
  next_button_selector: string | null
  item_selector: string
  expected_max_item_count: number | null
  query_param_key: string
  query_param_value_template: string
  start_value: number
  value_step: number
  domain: string | null
  path: string | null
  generated_from_crawl_id: string | null
  generated_from_artifact_id: string | null
  generated_by_task_run_id: string | null
  inputs_json: Record<string, unknown>
  validation_status: string | null
  failure_count: number
  last_failed_at: string | null
  last_error: string | null
  warnings_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type PaginationSchemaUpdateRequest = {
  match?: string
  enabled?: boolean
  priority?: number
  next_button_selector?: string | null
  item_selector?: string
  expected_max_item_count?: number | null
  query_param_key?: string
  query_param_value_template?: string
  start_value?: number
  value_step?: number
  validation_status?: string | null
}
