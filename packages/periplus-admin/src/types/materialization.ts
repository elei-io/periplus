export type BuildPhase = "preparing" | "building" | "verifying" | "ready" | "serving" | "previous" | "cancelling" | "cancelled" | "draining" | "retired"
export type BuildAction = "pause" | "resume" | "retry" | "cancel" | "activate"
export interface MaterialBuild {
  id: string
  phase: BuildPhase
  semantic_version: string
  material_database: string
  query_database: string
  consumer: string
  barrier: number | null
  ingestion_floor: number
  material_floor: number
  live_pending: number
  paused: boolean
  protected: boolean
  blocker: string | null
  created_at: string
  updated_at: string
  drain_after: string | null
  ranges: { month: number; cursor: string[] | null; done: boolean; processed: number; blocker: string | null }[]
}
