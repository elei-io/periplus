import type { CrawlProgressEvent, CrawlMode, CrawlWait } from "@/types/index"
import type { CrawlPage } from "@/types/crawl"

export type CalibrationTemplate =
  | "static_fast"
  | "static_wait"
  | "dynamic_scan"
  | "app_stable"
  | "app_deep"

export type CalibrateInput = {
  url: string
  force: boolean
}

export type CalibrationQuality = {
  html_bytes: number
  text_chars: number
  link_count: number
  warning_count: number
  warning_codes: string[]
}

export type CalibrationCandidate = {
  template: CalibrationTemplate
  mode: CrawlMode
  wait: CrawlWait
  run_config_overrides: Record<string, unknown>
  success: boolean
  accepted: boolean
  reason: string
  duration_seconds: number
  status_code: number | null
  quality: CalibrationQuality
  crawl_id: string | null
  artifact_ids: string[]
}

export type CrawlPolicyRecord = {
  id: string
  url_match_id: string | null
  match: string
  enabled: boolean
  config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type CalibrationOutput = {
  url: string
  match: string
  url_match_id: string
  policy: CrawlPolicyRecord
  reused_policy: boolean
  selected_template: CalibrationTemplate
  selected_config: Record<string, unknown>
  candidates: CalibrationCandidate[]
  selected_page: CrawlPage | null
}

export type CalibrateStreamEvent =
  | {
      type: "progress"
      data: CrawlProgressEvent
    }
  | {
      type: "result"
      data: CalibrationOutput
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
