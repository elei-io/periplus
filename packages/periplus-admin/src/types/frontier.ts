export type UrlExclusion = { host: string; path_prefix: string }

export type FrontierSettings = {
  paused: boolean
  exclusions: UrlExclusion[]
  collection_limit: number
  interest_limit: number
  acquisition_limit: number
  admission_limit: number
  dispatch_limit: number
  captures_per_minute: number | null
  attempt_allowance: number
  capture_time_allowance_ms: number
  capture_timeout_ms: number
}

export type FrontierControlView = {
  settings: FrontierSettings
  policy_version: number
  updated_at: string | null
  updated_by: string | null
  retained_acquisitions: number
  acquisition_admission_waiting_reason: string | null
  pending_acquisitions: number
  dispatched_acquisitions: number
  retained_interests: number
  reserved_attempts: number
  started_attempts: number
  reserved_capture_ms: number
  charged_capture_ms: number
  dispatch_waiting_reason: string | null
  allowance_semantics: "cumulative_until_operator_increases_limit"
  time_semantics: "client_capture_elapsed_not_provider_billing"
  next_rate_eligibility_at: string | null
  pause_behavior: "finish_started_captures"
  rate_semantics: "dispatch_upper_bound_null_is_unlimited"
  as_of: string
}

export type ReplaceFrontierSettings = {
  expected_version: number
  settings: FrontierSettings
}
