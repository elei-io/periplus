import { toast } from "sonner"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
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

export function useRetryIngestion() {
  const client = useQueryClient()
  return useMutation({ mutationFn: async (sequence: number) => {
    const response = await fetch(apiUrl(`/operations/repository/dead-letters/${sequence}/requeue`), { method: "POST" })
    if (!response.ok) throw await apiErrorFromResponse(response)
  }, onSuccess: () => { void client.invalidateQueries({ queryKey: ["ingestion"] }) },
  onError: error => toast.error(extractApiError(error)) })
}
