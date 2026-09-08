export interface StorageSource {
  id: string
  name: string
  bytes: number | null
  complete: boolean
  basis: string
  reason: string | null
}

export interface StorageEvidence {
  observations: number
  documents: number
  observations_with_documents: number
  unique_objects: number
  referenced_bytes: number
  unique_bytes: number
  median_bytes: number | null
  p95_bytes: number | null
}

export interface LakeStorageTable {
  schema_name: string
  name: string
  role: "evidence" | "projection" | "bookkeeping" | "other"
  generation: "current" | "rebuilding" | "retired"
  estimated_rows: number
  bytes: number
  data_bytes: number
  delete_bytes: number
  files: number
  delete_files: number
}

export interface StorageReport {
  as_of: string
  collected_at: string
  sources: StorageSource[]
  evidence: StorageEvidence | null
  tables: LakeStorageTable[]
  tables_complete: boolean
  control_tables: {
    name: string
    table_bytes: number
    index_bytes: number
    total_bytes: number
    estimated_rows: number
  }[]
  streams: { name: string; bytes: number | null; messages: number | null }[]
  retention: {
    stages: {
      id: string
      name: string
      objects: number
      expected_bytes: number
      oldest_at: string | null
    }[]
    retired_observations: number
    retired_requests: number
    latest_retirement_at: string | null
    oldest_snapshot_at: string | null
    snapshots: number
  } | null
  issues: string[]
}
