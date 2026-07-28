import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { DataStatus } from "@/types/operations"

export function useDataStatus() {
  return useQuery({
    queryKey: ["data-status"],
    refetchInterval: 5_000,
    queryFn: async () => {
      const response = await fetch(apiUrl("/operations/data-status"))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as DataStatus
    },
  })
}
