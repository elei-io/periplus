"use client"

import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { displayValue } from "@/lib/query-values"
import { extractApiError, responseJson } from "@/lib/api"
import type { QueryResult } from "@/types/sql"

export function useQueryExecution() {
  const mutation = useMutation({
    mutationFn: async ({ sql, parameters }: { sql: string; parameters: unknown[] }) => {
      const result = await responseJson<QueryResult>(await fetch("/api/query/exec", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ sql, parameters }),
      }))
      return { ...result, rows: result.rows.map(row => row.map(displayValue)) }
    },
    onError: error => toast.error(extractApiError(error)),
  })
  return { ...mutation, phase: mutation.isPending ? "Executing on Periplus" : mutation.isError ? "Failed" : mutation.isSuccess ? "Complete" : "Ready" }
}
