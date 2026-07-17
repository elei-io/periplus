export type PageParams = { limit: number; offset: number }

export type PaginatedResponse<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type ResponseOutcome = "retry" | "fail" | "skip" | "accept"

export type ContentPolicy = {
  accepted_content_types: string[]
  response_rules: {
    http_status: Array<{ minimum: number; maximum: number; outcome: ResponseOutcome }>
    unsupported_content_type: ResponseOutcome
  }
  completion: {
    navigation: {
      timeout_ms: number
      context_replacement_retries: number
      context_replacement_settle_ms: number
    }
    wait_dynamic: {
      enabled: boolean
      maximum_wait_ms: number
      sample_interval_ms: number
      stable_samples: number
    }
    wait_fixed: {
      enabled: boolean
      duration_ms: number
    }
    scroll: {
      enabled: boolean
      maximum_iterations: number
      viewport_ratio: number
      wait_ms: number
      stable_bottom_samples: number
    }
    expand: {
      enabled: boolean
      maximum_actions: number
      wait_ms: number
    }
  }
}

export type CrawlPolicyRecord = {
  id: string
  slug: string
  scheme: "*" | "http" | "https"
  host: string
  path_prefix: string
  path_mode: "exact" | "prefix"
  match: string
  content: ContentPolicy
  enabled: boolean
  created_at: string
  updated_at: string
}

export type CrawlPolicyDetailRecord = CrawlPolicyRecord
export type CrawlPolicyListResponse = PaginatedResponse<CrawlPolicyRecord>

export type CrawlPolicyUpdateRequest = Partial<
  Pick<CrawlPolicyRecord, "enabled" | "scheme" | "host" | "path_prefix" | "path_mode" | "content">
>

export type CrawlPolicyCreateRequest = Pick<
  CrawlPolicyRecord,
  "slug" | "scheme" | "host" | "path_prefix" | "path_mode" | "content" | "enabled"
>

export type CrawlPolicyFilters = {
  matchPattern: string
  enabled: "all" | "enabled" | "disabled"
}

export type DomainPolicyRecord = {
  id: string
  slug: string
  host_match: string
  maximum_concurrency: number
  minimum_request_interval_seconds: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export type DomainPolicyListResponse = PaginatedResponse<DomainPolicyRecord>
export type DomainPolicyCreateRequest = Pick<DomainPolicyRecord, "slug" | "host_match" | "maximum_concurrency" | "minimum_request_interval_seconds" | "enabled">
export type DomainPolicyUpdateRequest = Partial<Pick<DomainPolicyRecord, "host_match" | "maximum_concurrency" | "minimum_request_interval_seconds" | "enabled">>
