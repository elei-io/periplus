import type { CollectionSpec } from "@/types/collections"
export type RequestDefinition = {
  id: string
  name: string
  specification: CollectionSpec
  priority: number
  version: number
  created_at: string
}
export type ScheduleConfiguration = {
  kind: "interval" | "cron"
  interval_seconds: number | null
  cron: string | null
  timezone: string
  start_at: string
  stop_at: string | null
  max_count: number | null
  enabled: boolean
}
export type RequestSchedule = {
  id: string
  definition_id: string
  configuration: ScheduleConfiguration
  enabled: boolean
  version: number
  execution_count: number
  next_at: string | null
  last_request_id: string | null
  last_tick_at: string | null
  last_result: string | null
  created_at: string
}
