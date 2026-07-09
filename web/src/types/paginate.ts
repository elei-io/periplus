import type { CrawlProgressEvent } from "@/types/index"

export type PaginationKind = "query"

export type PaginateInput = {
  url: string
  max_pages: number
  mode: "static" | "dynamic" | "app"
  wait: "none" | "stable" | "network" | "fixed"
  reuse_existing: boolean
}

export type PaginationPlan = {
  schema_id: string | null
  kind: PaginationKind
  next_button_selector: string | null
  item_selector: string
  expected_max_item_count: number | null
  query_param_key: string
  query_param_value_template: string
  start_value: number
  value_step: number
  match: string
  reused: boolean
  confidence: number | null
  evidence: string[]
}

export type PaginatedPage = {
  index: number
  url: string
  crawl_id: string | null
  artifact_ids: string[]
  item_count: number
  new_item_count: number
  success: boolean
  error: string | null
}

export type PaginateOutput = {
  url: string
  pages: PaginatedPage[]
  plan: PaginationPlan | null
  stopped_reason: string
  warnings: string[]
}

export type PaginateStreamEvent =
  | {
      type: "progress"
      data: CrawlProgressEvent
    }
  | {
      type: "result"
      data: PaginateOutput
    }
  | {
      type: "done"
      data: Record<string, never>
    }
  | {
      type: "error"
      data: {
        message: string
      }
    }
