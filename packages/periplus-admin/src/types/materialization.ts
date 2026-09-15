export type BuildPhase = "preparing" | "building" | "verifying" | "ready" | "serving" | "previous" | "cancelling" | "cancelled" | "draining" | "retired"
export type BuildAction = "pause" | "resume" | "retry" | "cancel" | "activate"
export interface MaterialBuild {
  id: string
  phase: BuildPhase
  recipe: string
  manifest_key: string | null
  material_database: string
  query_database: string
  paused: boolean
  protected: boolean
  blocker: string | null
  created_at: string
  updated_at: string
  verified_at: string | null
  drain_after: string | null
  ranges: { shard: number; upper: number; cursor: number; live_cursor: number; processed: number }[]
  batches: { id: string; shard: number; lane: string; start: number; end: number; status: string; worker_id: string | null; attempts: number; error: string | null }[]
}
