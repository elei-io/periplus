import type { CrawlMode, CrawlProgressEvent, CrawlWait } from "@/types/index"

export type ExtractSchemaType = "css" | "xpath"

export type ExtractInput = {
  url: string
  prompt: string
  target_json_example: string | null
  schema_type: ExtractSchemaType
  mode: CrawlMode
  wait: CrawlWait
}

export type ExtractSource = {
  schema_id: string
  schema_type: ExtractSchemaType
}

export type QualityWarning = {
  code: string
  name: string
  description: string
  signals: Array<{
    name: string
    value: unknown
  }>
}

export type ExtractOutput = {
  url: string
  success: boolean
  source: ExtractSource | null
  results: Array<Record<string, unknown>>
  warnings: QualityWarning[]
  error: string | null
}

export type ExtractStreamEvent =
  | {
      type: "progress"
      data: CrawlProgressEvent
    }
  | {
      type: "result"
      data: ExtractOutput
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
