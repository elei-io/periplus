"use client"

import { usePublicAccess } from "@/hooks/use-public-access"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { extractApiError, responseJson } from "@/lib/api"
import type { QueryResult } from "@/types/sql"

export function useQueryExecution() {
  const access = usePublicAccess("sql")
  const mutation = useMutation({
    mutationFn: async ({ sql, parameters }: { sql: string; parameters: unknown[] }) => {
      if (!access.enabled) throw new Error(access.message ?? "SQL execution is unavailable.")
      const result = await responseJson<QueryResult>(await fetch("/api/query/exec", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ sql, parameters }),
      }))
      return result
    },
    onError: error => { access.onDenied(error); toast.error(extractApiError(error)) },
  })
  return { ...mutation, access, phase: mutation.isPending ? "Executing on Periplus" : mutation.isError ? "Failed" : mutation.isSuccess ? "Complete" : "Ready" }
}
