import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { OperationsMetricsResponse } from "@/types/operations"

export function useOperationsMetrics(windowSeconds: number) {
  return useQuery({
    queryKey: ["operations-metrics", windowSeconds],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/operations/metrics?window_seconds=${windowSeconds}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as OperationsMetricsResponse
    },
    refetchInterval: 10_000,
  })
}
