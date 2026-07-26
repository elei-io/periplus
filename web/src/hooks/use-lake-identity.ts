import type { CatalogueStatus } from "@atlas/console-core"
import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"

export function useLakeIdentity() {
  return useQuery({
    queryKey: ["catalogue-status"],
    queryFn: async () => {
      const response = await fetch(apiUrl("/catalogue/status"))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as CatalogueStatus
    },
    staleTime: 60_000,
  })
}
