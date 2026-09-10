import { z } from "zod"

export type Capability = "crawl" | "assistant" | "sql"
const ratePolicySchema = z.object({ enabled: z.boolean(), requests: z.number().int().positive(), window_seconds: z.number().int().positive() })
export type RatePolicy = z.infer<typeof ratePolicySchema>
const accessPolicySchema = z.object({
  version: z.number().int(),
  crawl_admission: z.object({ pending_acquisitions: z.number().int().nonnegative(), accepting: z.boolean() }),
  crawl: ratePolicySchema.extend({
    queue_limit: z.number().int().positive().nullable(),
    page_budgets: z.array(z.number().int().positive()), default_page_budget: z.number().int().positive(),
    follow_link_limits: z.array(z.number().int().positive()), default_follow_link_limit: z.number().int().positive(),
    max_depths: z.array(z.number().int().nonnegative()), default_max_depth: z.number().int().nonnegative(),
    retention_seconds: z.array(z.number().int().positive().nullable()), default_retention_seconds: z.number().int().positive().nullable(),
  }),
  assistant: ratePolicySchema,
  sql: ratePolicySchema.extend({ max_rows: z.number().int().positive(), max_duration_seconds: z.number().int().positive(), max_result_bytes: z.number().int().positive() }),
})
export type AccessPolicy = z.infer<typeof accessPolicySchema>

export function parseAccessPolicy(value: unknown): AccessPolicy {
  const result = accessPolicySchema.safeParse(value)
  if (!result.success) throw new Error("Public access settings are incomplete or invalid. Please try again shortly.")
  return result.data
}
