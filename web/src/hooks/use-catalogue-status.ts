import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"

type CatalogueStatusResponse = {
  active_file_count: number
  active_storage_bytes: number
  ducklake_version: string | null
  catalogue_schema_version: number
}

export type CatalogueStatus = CatalogueStatusResponse & {
  apiLatencyMs: number
}

async function fetchCatalogueStatus(): Promise<CatalogueStatus> {
  const startedAt = performance.now()
  const response = await fetch(apiUrl("/catalogue/status"))
  if (!response.ok) throw await apiErrorFromResponse(response)
  const status = (await response.json()) as CatalogueStatusResponse

  return {
    ...status,
    apiLatencyMs: performance.now() - startedAt,
  }
}

export function useCatalogueStatus() {
  return useQuery({
    queryKey: ["catalogue-status"],
    queryFn: fetchCatalogueStatus,
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}
