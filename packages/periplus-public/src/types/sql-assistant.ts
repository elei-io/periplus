import { z } from "zod"

export const sqlDraftSchema = z.object({
  sql: z.string().max(20_000),
  parameters: z.string().max(8_000).refine(value => {
    try { return Array.isArray(JSON.parse(value)) } catch { return false }
  }, "Parameters must be a JSON array."),
})

export const sqlAssistantInputSchema = z.object({
  queryMode: z.enum(["stable", "experimental"]).default("stable"),
  intent: z.string().trim().min(1).max(4_000),
  draft: sqlDraftSchema,
  proposal: sqlDraftSchema.nullable(),
  selection: z.string().max(20_000),
  failure: z.object({ sql: z.string().max(20_000), parameters: z.string().max(8_000), message: z.string().max(2_000) }).nullable(),
  history: z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(12_000) })).max(20),
})

// Keep the provider's output schema simple; validate parameter JSON separately.
export const sqlSuggestionSchema = z.object({
  message: z.string().min(1).max(12_000),
  sql: z.string().min(1).max(20_000).nullable(),
  parameters: z.string().max(8_000),
})

export type SqlDraft = z.infer<typeof sqlDraftSchema>
export type SqlAssistantInput = z.infer<typeof sqlAssistantInputSchema>
export type SqlAssistantReply = z.infer<typeof sqlSuggestionSchema> & {
  validation: { status: "prepared" | "unavailable" | "invalid"; message: string } | null
}

export function sameSqlDraft(left: SqlDraft, right: SqlDraft) {
  return left.sql === right.sql && left.parameters === right.parameters
}

export type SqlAssistantActivity = {
  id: string
  label: string
  status: "running" | "complete" | "error"
  sql?: string
  detail?: string
  elapsedMs?: number
}
export type SqlAssistantEvent =
  | { type: "activity"; activity: SqlAssistantActivity }
  | { type: "result"; reply: SqlAssistantReply }
  | { type: "error"; message: string }
