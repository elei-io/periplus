export type Capability = "crawl" | "assistant" | "sql"
export type RatePolicy = { enabled: boolean; requests: number; window_seconds: number }
export type AccessPolicy = {
  version: number
  crawl_admission: { pending_acquisitions: number; accepting: boolean }
  crawl: RatePolicy & { queue_limit: number | null; page_budgets: number[]; default_page_budget: number; follow_link_limits: number[]; default_follow_link_limit: number; max_depths: number[]; default_max_depth: number; retention_seconds: (number | null)[]; default_retention_seconds: number | null }
  assistant: RatePolicy
  sql: RatePolicy & { max_rows: number; max_duration_seconds: number; max_result_bytes: number }
}
