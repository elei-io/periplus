import type { CrawlProgressEvent } from "@/types/index"

export type SearchInput = {
  query: string
  max_results: number
}

export type SearchResult = {
  url: string
  title: string
  description: string
}

export type SearchStreamEvent =
  | {
      type: "progress"
      data: CrawlProgressEvent
    }
  | {
      type: "result"
      data: SearchResult[]
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
