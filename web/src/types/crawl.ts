import type { ProgressEvent } from "@/types/progress"
import type { QualityWarning } from "@/types/extract"

export type CrawlInput = {
  urls: string[]
}

export type CrawlPage = {
  url: string
  success: boolean
  status_code: number | null
  duration_seconds: number
  html: string | null
  crawl: Record<string, unknown> | null
  artifact_warnings: QualityWarning[]
  error: string | null
}

export type CrawlStats = {
  requested_urls: number
  succeeded: number
  failed: number
  duration_seconds: number
}

export type CrawlOutput = {
  stats: CrawlStats
  pages: CrawlPage[]
}

export type CrawlStreamEvent =
  | {
      type: "progress"
      data: ProgressEvent
    }
  | {
      type: "result"
      data: CrawlOutput
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
