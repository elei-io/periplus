import type { ProgressEvent } from "@/types/progress"

export type SearchProvider = "duckduckgo" | "brave" | "yahoo"

export const searchProviders: Array<{ value: SearchProvider; label: string }> = [
  { value: "duckduckgo", label: "DuckDuckGo" },
  { value: "brave", label: "Brave" },
  { value: "yahoo", label: "Yahoo" },
]

export type SearchInput = {
  query: string
  max_pages: number
  provider: SearchProvider
}

export type SearchResult = {
  url: string
  title: string
  description: string
}

export type SearchStreamEvent =
  | {
      type: "progress"
      data: ProgressEvent
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
