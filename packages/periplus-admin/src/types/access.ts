export type Capability = "crawl" | "assistant" | "sql"
export type RatePolicy = { enabled: boolean; requests: number; window_seconds: number }
export type AccessPolicy = {
  version: number
  crawl: RatePolicy & { page_budgets: number[]; default_page_budget: number; max_depths: number[]; default_max_depth: number; retention_seconds: (number | null)[]; default_retention_seconds: number | null }
  assistant: RatePolicy
  sql: RatePolicy
}
