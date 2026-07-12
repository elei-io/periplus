export type CrawlMode = "static" | "dynamic" | "app"

export type CrawlWait = "none" | "stable" | "network" | "fixed"

import type { ProgressEvent } from "@/types/progress"
import type { CacheOptions } from "@/types/cache"

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
  include_crawl: string[]
  exclude_crawl: string[]
  include_result: string[]
  exclude_result: string[]
  cache?: CacheOptions | null
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

export type IndexOutput = {
  pages: number
  failed_pages: number
  discovered_links: number
  result_links: number
  internal_links: number
  external_links: number
  sample_links: IndexLink[]
}

export type IndexStreamEvent =
  | {
      type: "progress"
      data: ProgressEvent
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
