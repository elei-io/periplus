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
