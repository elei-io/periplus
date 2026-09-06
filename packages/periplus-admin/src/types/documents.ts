import type { PaginatedResponse } from "@/types/resources"

export type DocumentSort =
  | "url"
  | "content_type"
  | "representation"
  | "observed_at"
  | "content_bytes"
  | "stored_bytes"

export type SortDirection = "asc" | "desc"

export type DocumentRecord = {
  document_id: string
  visit_id: string
  attempt_id: string | null
  url: string
  observed_at: string
  representation: "response_body" | "rendered_html"
  declared_media_type: string | null
  detected_media_type: string
  charset: string | null
  content_sha256: string
  content_bytes: number
  storage_encoding: string
  stored_bytes: number
}

export type DocumentSummary = {
  document_count: number
  unique_content_count: number
  logical_bytes: number
  stored_bytes: number
}

export type DocumentListResponse = PaginatedResponse<DocumentRecord> & {
  summary: DocumentSummary
}

export type DocumentListParams = {
  limit: number
  offset: number
  sort: DocumentSort
  direction: SortDirection
  contentType?: string
  url?: string
  observedFrom?: string
  observedTo?: string
}
