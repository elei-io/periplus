export type UrlExclusion = { host: string; path_prefix: string }

export type FrontierSettings = {
  paused: boolean
  exclusions: UrlExclusion[]
  dispatch_limit: number
  capture_timeout_ms: number
}

export type FrontierControlView = {
  settings: FrontierSettings
  policy_version: number
  updated_at: string | null
  updated_by: string | null
  retained_acquisitions: number
  pending_acquisitions: number
  dispatched_acquisitions: number
  retained_interests: number
  dispatch_waiting_reason: string | null
  pause_behavior: "finish_started_captures"
  as_of: string
}

export type ReplaceFrontierSettings = {
  expected_version: number
  settings: FrontierSettings
}
