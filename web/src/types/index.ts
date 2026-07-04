export type CrawlMode = "static" | "dynamic" | "app"

export type CrawlWait = "none" | "stable" | "network" | "fixed"

export type CrawlProgressStatus = "started" | "succeeded" | "failed"

export type IndexFilterType =
  | "include_crawl"
  | "exclude_crawl"
  | "include_result"
  | "exclude_result"

export type IndexFilterRow = {
  id: string
  type: IndexFilterType
  value: string
}

export type IndexResultScope = "all" | "internal" | "external"

export type IndexInput = {
  url: string
  max_depth: number
  dedupe: boolean
  concurrency: number
  mode: CrawlMode
  wait: CrawlWait
  include_crawl: string[]
  exclude_crawl: string[]
  include_result: string[]
  exclude_result: string[]
}

export type IndexLink = {
  source_url: string
  url: string
  text: string
  title: string
  depth: number
  link_index: number
  internal: boolean
}

export type CrawlProgressEvent = {
  url: string
  status: CrawlProgressStatus
  label: string
  duration: number | null
  error: string | null
}

export type IndexStreamEvent =
  | {
      type: "progress"
      data: CrawlProgressEvent
    }
  | {
      type: "result"
      data: IndexLink[]
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
