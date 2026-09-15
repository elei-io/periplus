export interface StorageSource {
  id: string; name: string; bytes: number | null; complete: boolean; basis: string; reason: string | null
}
export interface StorageReport {
  collected_at: string
  sources: StorageSource[]
  tables: { database: string; name: string; rows: number; bytes: number; uncompressed_bytes: number; parts: number }[]
  disks: { name: string; total_space: number; free_space: number }[]
  merges: { database: string; name: string; elapsed: number; progress: number; memory_usage: number }[]
  control_tables: { name: string; bytes: number; estimated_rows: number }[]
  streams: { name: string; bytes: number; messages: number }[]
}
