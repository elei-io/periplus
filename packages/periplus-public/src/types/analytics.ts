export type AnalyticsFlow = "sql" | "discovery"
export type AnalyticsOutcome = "success" | "failed" | "cancelled" | "blocked" | "sample" | "draft"
export type AnalyticsProperties = Record<string, string | number | boolean | null | undefined>
export interface AnalyticsOperation { id: string; started: number; finished: boolean }
export interface DiscoveryAnalyticsMetadata {
  operation_id: string
  model: string
  input_tokens?: number
  output_tokens?: number
}
