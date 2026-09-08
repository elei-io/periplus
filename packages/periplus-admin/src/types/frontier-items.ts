export interface StartEstimate {
  earliest_at: string
  latest_at: string
  calculated_at: string
  expires_at: string
  sample_size: number
  sample_window_seconds: 600
  basis: "recent_comparable_observed_wait_range"
  uncertainty: "conditional_not_a_guarantee"
  assumptions: "unchanged_policies_worker_readiness_and_competing_work"
}

export interface AcquisitionView {
  id: string
  url: string
  domain: string
  status: string
  created_at: string
  completed_at: string | null
  attempt_count: number
  observation_id: string | null
  evidence_committed: boolean
  terminal_reason: string | null
  query_ready: boolean | null
  query_readiness_reason: string
  eligibility_not_before: string | null
  waiting_reason: string | null
  next_start_estimate: StartEstimate | null
  estimate_unavailable_reason: string | null
  callers: Array<{
    collection_id: string
    interest_id: string
    status: string
    mode: string
  }>
  more_callers: boolean
  as_of: string
}
export interface CollectionItemsPage {
  source: "current"
  collection_id: string
  items: Array<{
    interest_id: string
    status: string
    mode: string
    budget_state: string
    context: {
      depth: number
      parent_observation_id: string | null
      rule_id: string
    }
    admitted_at: string
    acquisition: AcquisitionView
  }>
  next_after: string | null
  as_of: string
  ordering: "interest_identity_not_dispatch_order"
}

export interface CollectionArrivalsPage {
  source: "history"
  collection_id: string
  definition_committed: boolean
  items: Array<{
    fulfillment_id: string
    observation_id: string
    requested_url: string
    parent_observation_id: string | null
    depth: number
    rule_id: string
    mode: "acquired" | "shared" | "reused"
    decided_at: string
    observation_committed: boolean
    effective_url: string | null
    observed_at: string | null
    outcome: string | null
    http_status_code: number | null
    query_ready: boolean | null
    query_readiness_reason: string
  }>
  next_cursor: string | null
  as_of: string
}

export interface ObservationLineagePage {
  observation_id: string
  requested_url: string
  items: Array<{
    record_id: string
    kind: "fulfillment" | "reason"
    decided_at: string
    collection_id: string
    parent_observation_id: string | null
    rule_id: string
    depth: number | null
    mode: "acquired" | "shared" | "reused" | null
    reason: "collection" | null
    policy_version: string | null
  }>
  next_cursor: string | null
  as_of: string
  completeness: "committed_visible_evidence_only_ingestion_may_lag"
}
