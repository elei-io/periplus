import { useQuery } from "@tanstack/react-query"
import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { StorageReport } from "@/types/storage"

export function useStorage() {
  return useQuery({
    queryKey: ["storage"],
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl("/operations/storage"), {
        signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<StorageReport>
    },
    staleTime: 60000,
    refetchInterval: 60000,
    retry: false,
  })
}
