"use client"

import { useQuery } from "@tanstack/react-query"
import { responseJson } from "@/lib/api"
import type { QueryResult } from "@/types/sql"

class QueryBusyError extends Error {
  constructor(readonly retryAfterMs: number) { super("The query server is busy. Please try again shortly.") }
}

export function useDatasetResult(sql: string, refetchInterval: number | false = false) {
  return useQuery({
    queryKey: ["dataset-result", sql],
    // Preserve a shared in-flight read across remounts. Disconnecting does not
    // guarantee server cancellation; abandoning it and retrying can duplicate work.
    queryFn: async () => {
      const response = await fetch("/api/query/exec", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ sql, parameters: [] }),
      })
      if (response.status === 429) {
        const seconds = Number(response.headers.get("retry-after") ?? "1")
        throw new QueryBusyError(Number.isFinite(seconds) ? Math.min(10_000, Math.max(1_000, seconds * 1_000)) : 1_000)
      }
      return responseJson<QueryResult>(response)
    },
    staleTime: 60_000,
    refetchInterval,
    retry: (failures, error) => error instanceof QueryBusyError && failures < 2,
    retryDelay: (_attempt, error) => error instanceof QueryBusyError ? error.retryAfterMs : 1_000,
    refetchOnWindowFocus: false,
  })
}
