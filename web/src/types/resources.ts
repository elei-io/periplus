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
  profile: string | null
  mode: string | null
  wait: string | null
  concurrency: number | null
  created_at: string
  updated_at: string
}

export type CrawlPolicyDetailRecord = Omit<
  CrawlPolicyRecord,
  "template" | "profile" | "mode" | "wait" | "concurrency"
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

export type PolicyTrialComparison = {
  scheme: "http" | "https"
  host: string
  port: number
  registrable_domain: string
  use_template: string
  candidate_template: string
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
  current_template: string
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
  template: string
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
