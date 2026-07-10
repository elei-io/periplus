export type TaskPrimitive =
  | "search"
  | "index"
  | "crawl"
  | "schema"
  | "extract"
  | "calibrate"

export type OnceSchedule = {
  kind: "once"
  run_at: string
  timezone: string
}

export type CronSchedule = {
  kind: "cron"
  expr: string
  timezone: string
  start_at?: string | null
  end_at?: string | null
}

export type IntervalSchedule = {
  kind: "interval"
  every_seconds: number
  timezone: string
  start_at?: string | null
  end_at?: string | null
}

export type TaskSchedule = OnceSchedule | CronSchedule | IntervalSchedule

export type TaskRecord = {
  id: string
  name: string
  primitive: TaskPrimitive
  input_json: Record<string, unknown>
  schedule_json: TaskSchedule | null
  identity_key: string | null
  created_by_effect_run_id: string | null
  updated_by_effect_run_id: string | null
  archived_by_effect_run_id: string | null
  archived_at: string | null
  archived_reason: string | null
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
  updated_at: string
}

export type TaskCreate = {
  name: string
  primitive: TaskPrimitive
  input: Record<string, unknown>
  schedule: TaskSchedule | null
  identity_key?: string | null
}

export type TaskUpdate = Partial<TaskCreate> & {
  archived_at?: string | null
  archived_reason?: string | null
}

export type TaskFilters = {
  primitive?: TaskPrimitive
  archived?: boolean
  origin?: "human" | "effect"
}
