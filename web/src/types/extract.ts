import type { ProgressEvent } from "@/types/progress"
import type { CacheOptions } from "@/types/cache"

export type DataSchemaType = "css" | "xpath"

export type ExtractInput = {
  url: string
  extract_data: boolean
  extract_query_params: boolean
  prompt: string | null
  target_json_example: string | null
  schema_type: DataSchemaType
  cache?: CacheOptions | null
}

export type ExtractSource = {
  schema_id: string
  schema_type: DataSchemaType
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

export type QueryParamCandidate = {
  url: string
  source: string
  selector: string | null
  text: string | null
  attributes: Record<string, string>
}

export type QueryParamResult = {
  value: string
  label: string | null
  source: string | null
  confidence: number
}

export type QueryParamGroup = {
  key: string
  kind: "text" | "enum" | "range" | "sort" | "pagination" | "state" | "unknown"
  pagination_role: "next" | "prev" | "index" | null
  best_effort_description: string
  confidence: number
  values: QueryParamResult[]
}

export type QuerySchema = {
  schema_type: string
  extraction_schema: Record<string, unknown>
}

export type QueryParamOutput = {
  url: string
  crawl_id: string | null
  document_id: string | null
  candidates: QueryParamCandidate[]
  query_schema: QuerySchema | null
  params: QueryParamGroup[]
  warnings: string[]
}

export type ExtractOutput = {
  url: string
  success: boolean
  source: ExtractSource | null
  results: Array<Record<string, unknown>>
  query_params: QueryParamOutput | null
  warnings: QualityWarning[]
  error: string | null
}

export type ExtractStreamEvent =
  | {
      type: "progress"
      data: ProgressEvent
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
