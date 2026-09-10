"use client"

import { useEffect, useRef } from "react"
import { startAnalyticsOperation, finishAnalyticsOperation, analyticsErrorCategory } from "@/lib/analytics"
import { usePublicAccess } from "@/hooks/use-public-access"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { extractApiError, responseJson } from "@/lib/api"
import type { QueryMode, QueryResult } from "@/types/sql"

export function useQueryExecution() {
  const controllers = useRef(new Set<AbortController>())
  useEffect(() => { const active = controllers.current; return () => { active.forEach(controller => controller.abort()); active.clear() } }, [])
  const access = usePublicAccess("sql")
  const mutation = useMutation({
    mutationFn: async ({ sql, parameters, mode = "stable" }: { sql: string; parameters: unknown[]; mode?: QueryMode }) => {
      if (!access.enabled) throw new Error(access.message ?? "SQL execution is unavailable.")
      const operation = startAnalyticsOperation("sql_query_started", { flow: "sql", has_parameters: parameters.length > 0 })
      const controller = new AbortController()
      controllers.current.add(controller)
      try {
        const result = await responseJson<QueryResult>(await fetch(mode === "experimental" ? "/api/query/experimental/exec" : "/api/query/exec", {
          method: "POST", headers: { "content-type": "application/json", "x-periplus-operation-id": operation.id },
          body: JSON.stringify({ sql, parameters }), signal: controller.signal,
        }))
        if (result.query_mode !== mode) throw new Error("The query endpoint returned the wrong execution mode.")
        finishAnalyticsOperation(operation, "sql_query_finished", { flow: "sql", outcome: "success", row_count: result.rows.length, truncated: result.truncated, query_id: result.query_id, execution_ms: result.elapsed_ms })
        return { ...result, operationId: operation.id }
      } catch (error) {
        const category = analyticsErrorCategory(error)
        finishAnalyticsOperation(operation, "sql_query_finished", { flow: "sql", outcome: category === "cancelled" ? "cancelled" : "failed", error_category: category })
        throw error
      } finally { controllers.current.delete(controller) }
    },
    onError: error => { access.onDenied(error); toast.error(extractApiError(error)) },
  })
  return { ...mutation, operationId: mutation.data?.operationId, access, phase: mutation.isPending ? "Executing on Periplus" : mutation.isError ? "Failed" : mutation.isSuccess ? "Complete" : "Ready" }
}
