import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { MaterializationRun } from "@/types/operations"

export function useMaterializationRuns() {
  return useQuery({
    queryKey: ["materialization-runs"],
    queryFn: async ({ signal }) => {
      const response = await fetch(
        apiUrl("/operations/materializations/runs?limit=100"),
        { signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]) }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as MaterializationRun[]
    },
    refetchInterval: 5_000,
    staleTime: 5_000,
    retry: false,
  })
}
