import { useQuery } from "@tanstack/react-query"
import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { IngestionReport } from "@/types/ingestion"

export function useIngestion() {
  return useQuery({
    queryKey: ["ingestion"],
    refetchInterval: 10_000,
    staleTime: 5_000,
    retry: false,
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl("/operations/ingestion"), {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as IngestionReport
    },
  })
}
