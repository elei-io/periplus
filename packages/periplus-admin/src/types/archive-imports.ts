export interface ArchiveImportSpec {
  dataset: string
  urls: string[]
  captured_from: string
  captured_until: string
  max_download_bytes: number
}
export interface ArchiveImport {
  id: string
  specification: ArchiveImportSpec
  status: "queued" | "running" | "blocked" | "completed" | "cancelled"
  error: string | null
  created_at: string
  updated_at: string
  progress: {
    cursor: number
    reserved_download_bytes: number
    results: {
      url: string
      status: "published" | "missing" | "unsupported" | "retired"
      capture_id: string | null
      captured_at: string | null
      content_sha256: string | null
      stored_bytes: number
      already_archived: boolean
      detail: string | null
    }[]
  }
}
export interface CreateArchiveImport {
  id: string
  specification: ArchiveImportSpec
}
