import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { CatalogueMetadata } from "@/types/catalogue"

async function fetchCatalogueMetadata(): Promise<CatalogueMetadata> {
  const response = await fetch(apiUrl("/catalogue/metadata"))
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<CatalogueMetadata>
}

export function useCatalogueMetadata() {
  return useQuery({
    queryKey: ["catalogue-metadata"],
    queryFn: fetchCatalogueMetadata,
    refetchInterval: 60_000,
  })
}
