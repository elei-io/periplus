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

export type QuerySchemaRecord = {
  id: string
  url_match_id: string | null
  match: string
  enabled: boolean
  priority: number
  schema_type: string
  domain: string | null
  path: string | null
  schema_hash: string
  param_count: number
  evidence_count: number
  warning_count: number
  generated_from_crawl_id: string | null
  generated_from_document_id: string | null
  generated_by_task_run_id: string | null
  created_at: string
  updated_at: string
}

export type QuerySchemaListResponse = PaginatedResponse<QuerySchemaRecord>

export type QuerySchemaDetailRecord = QuerySchemaRecord & {
  identity_key: string
  extraction_schema: Record<string, unknown>
  params_json: Array<Record<string, unknown>>
  evidence_json: Array<Record<string, unknown>>
  inputs_json: Record<string, unknown>
  warnings_json: Record<string, unknown>
}

export type QuerySchemaUpdateRequest = {
  enabled?: boolean
  priority?: number
}

export type QuerySchemaFilters = {
  matchPattern: string
  domain: string
  schemaType: "all" | "css" | "xpath"
  enabled: "all" | "enabled" | "disabled"
  warnings: "all" | "clean" | "warning"
}

export type DataSchemaRecord = {
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

export type DataSchemaListResponse = PaginatedResponse<DataSchemaRecord> & {
  summary: DataSchemaSummary
}

export type DataSchemaSummary = {
  total_schemas: number
  enabled_schemas: number
  used_schemas: number
  total_schema_uses: number
  reused_schema_uses: number
  reuse_rate: number
  avg_uses_per_used_schema: number
  failing_schemas: number
}

export type DataSchemaDetailRecord = DataSchemaRecord & {
  identity_key: string
  schema_json: Record<string, unknown>
  generated_from_crawl_id: string | null
  generated_from_document_id: string | null
  generated_by_task_run_id: string | null
  inputs_json: Record<string, unknown>
  warnings_json: Record<string, unknown>
}

export type DataSchemaUpdateRequest = {
  match?: string
  enabled?: boolean
  priority?: number
  schema_json?: Record<string, unknown>
  validation_status?: string | null
}

export type DataSchemaFilters = {
  matchPattern: string
  prompt: string
  schemaType: "all" | "css" | "xpath"
  enabled: "all" | "enabled" | "disabled"
  warnings: "all" | "clean" | "warning"
}

export type CrawlPolicyRecord = {
  id: string
  metric_slug: string
  domain_group: string
  url_match_id: string | null
  match: string
  enabled: boolean
  config: Record<string, unknown>
  revision: number
  template: string | null
  mode: string | null
  wait: string | null
  max_concurrency: number | null
  created_at: string
  updated_at: string
}

export type CrawlPolicyDetailRecord = Omit<
  CrawlPolicyRecord,
  "template" | "mode" | "wait" | "max_concurrency"
>

export type CrawlPolicyListResponse = PaginatedResponse<CrawlPolicyRecord>

export type CrawlPolicyUpdateRequest = {
  enabled?: boolean
  match?: string
  config?: Record<string, unknown>
  domain_group?: string
}

export type CrawlPolicyFilters = {
  matchPattern: string
  enabled: "all" | "enabled" | "disabled"
  template: string
  mode: "all" | "static" | "dynamic" | "app"
}
