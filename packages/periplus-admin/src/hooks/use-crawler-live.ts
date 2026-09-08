import { useQuery } from "@tanstack/react-query"
import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { LiveView } from "@/types/crawler-live"

export function useCrawlerLive() {
  return useQuery({
    queryKey: ["crawler-live"],
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl("/frontier/live"), {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as LiveView
    },
    refetchInterval: 5_000,
    retry: false,
  })
}
