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

export type CrawlTransport = "http" | "browser" | "firecrawl"

export type CrawlProfileRecord = {
  id: string
  slug: string
  name: string
  description: string | null
  transport: CrawlTransport
  config: Record<string, unknown>
  cost_rank: number
  trial_eligible: boolean
  created_at: string
  updated_at: string
}

export type CrawlProfileListResponse = PaginatedResponse<CrawlProfileRecord>

export type CrawlProfileUpdateRequest = {
  name?: string
  description?: string
  config?: Record<string, unknown>
  cost_rank?: number
  trial_eligible?: boolean
}

export type CrawlProfileCreateRequest = {
  slug: string
  name: string
  description?: string
  transport: CrawlTransport
  config: Record<string, unknown>
  cost_rank: number
  trial_eligible: boolean
}

export type CrawlPolicyRecord = {
  id: string
  slug: string
  scheme: "*" | "http" | "https"
  host: string
  path_prefix: string
  path_mode: "exact" | "prefix"
  match: string
  profile: CrawlProfileRecord
  max_concurrency: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export type CrawlPolicyDetailRecord = CrawlPolicyRecord
export type CrawlPolicyListResponse = PaginatedResponse<CrawlPolicyRecord>

export type CrawlPolicyUpdateRequest = {
  enabled?: boolean
  scheme?: "*" | "http" | "https"
  host?: string
  path_prefix?: string
  path_mode?: "exact" | "prefix"
  profile_id?: string
  max_concurrency?: number
}

export type CrawlPolicyCreateRequest = {
  slug: string
  scheme: "*" | "http" | "https"
  host: string
  path_prefix: string
  path_mode: "exact" | "prefix"
  profile_id: string
  max_concurrency: number
  enabled: boolean
}

export type CrawlPolicyFilters = {
  matchPattern: string
  enabled: "all" | "enabled" | "disabled"
  profileSlug: string
  transport: "all" | CrawlTransport
}

export type PolicyTrialComparison = {
  scheme: "http" | "https"
  host: string
  port: number
  registrable_domain: string
  use_profile: string
  candidate_profile: string
  use_profile_config_hash: string
  candidate_profile_config_hash: string
  candidate_profile_definition_hash: string
  selected_trials: number
  completed_pairs: number
  recovered_crawls: number
  sample_failures: number
  identical_documents: number
  median_html_delta_percent: number | null
  median_visible_text_delta_percent: number | null
  median_element_delta_percent: number | null
  mean_use_visible_text_chars: number | null
  mean_sample_visible_text_chars: number | null
  use_visible_text_stddev: number | null
  sample_visible_text_stddev: number | null
  use_visible_text_cv: number | null
  sample_visible_text_cv: number | null
  use_distinct_document_ratio: number | null
  sample_distinct_document_ratio: number | null
  median_use_quality_flag_count: number | null
  median_sample_quality_flag_count: number | null
  use_acquisition_failure_count: number
  sample_acquisition_failure_count: number
  median_duration_delta_ms: number | null
  last_trial_at: string
  current_policy_id: string | null
  current_profile: string
  applied: boolean
  verdict:
    | "awaiting_sample"
    | "insufficient_evidence"
    | "promising"
    | "no_clear_gain"
    | "regressed"
    | "inconclusive"
  verdict_reason: string
}

export type PolicyTrialApplyRequest = Pick<
  PolicyTrialComparison,
  "scheme" | "host" | "port"
> & {
  profile: string
}

export type PolicyTrialSummary = {
  sampling_active: boolean
  configured_sample_rate: number
  observed_sample_rate: number
  max_in_flight: number
  use_crawls: number
  selected_trials: number
  sample_crawls: number
  completed_pairs: number
  awaiting_samples: number
  pairs_with_failure: number
  last_trial_at: string | null
}

export type PolicyTrialReport = PaginatedResponse<PolicyTrialComparison> & {
  summary: PolicyTrialSummary
}
