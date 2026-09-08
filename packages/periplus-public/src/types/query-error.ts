import { z } from "zod"

export const queryErrorSchema = z.object({
  code: z.enum(["sql_invalid", "helper_limit", "resource_limit", "storage_unavailable", "access_unavailable", "service_busy", "query_failed"]),
  detail: z.string(),
})
export type QueryErrorCode = z.infer<typeof queryErrorSchema>["code"] | "service_unavailable"
