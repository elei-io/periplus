export type AnalyticsFlow = "sql" | "discover" | "build"
export type AnalyticsOutcome = "success" | "failed" | "cancelled" | "completed"
export type AnalyticsProperties = Record<string, string | number | boolean | null | undefined>
export interface AnalyticsOperation { id: string; started: number; finished: boolean }
export interface DiscoveryAnalyticsMetadata {
  operation_id: string
  model: string
  input_tokens?: number
  output_tokens?: number
}
