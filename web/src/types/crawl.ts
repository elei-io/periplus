import type { CrawlMode, CrawlProgressEvent, CrawlWait } from "@/types/index"
import type { QualityWarning } from "@/types/extract"

export type CrawlInput = {
  urls: string[]
  mode: CrawlMode
  wait: CrawlWait
  concurrency: number
}

export type CrawlPage = {
  url: string
  success: boolean
  status_code: number | null
  duration_seconds: number
  html: string | null
  crawl: Record<string, unknown> | null
  warnings: QualityWarning[]
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
      data: CrawlProgressEvent
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
