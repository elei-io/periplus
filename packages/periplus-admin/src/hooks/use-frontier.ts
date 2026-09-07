import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  FrontierControlView,
  ReplaceFrontierSettings,
} from "@/types/frontier"

export function useFrontierControls() {
  return useQuery({
    queryKey: ["frontier-controls"],
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl("/frontier/controls"), {
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as FrontierControlView
    },
    refetchInterval: 5000,
    retry: false,
  })
}

export function useReplaceFrontierControls() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (payload: ReplaceFrontierSettings) => {
      const response = await fetch(apiUrl("/frontier/controls"), {
        method: "PUT",
        signal: AbortSignal.timeout(15000),
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as FrontierControlView
    },
    onSuccess: (state) => {
      client.setQueryData(["frontier-controls"], state)
      toast.success("Crawler settings updated.")
    },
    onError: (error) => {
      toast.error(extractApiError(error))
      void client.invalidateQueries({ queryKey: ["frontier-controls"] })
    },
  })
}
