import { useQuery } from "@tanstack/react-query"

import { readQuackCatalogueStatus } from "@/components/catalogue/workbench-query-runtime"

type CatalogueStatusResponse = {
  active_file_count: number
  active_storage_bytes: number
  ducklake_version: string | null
  catalogue_schema_version: string
}

export type CatalogueStatus = CatalogueStatusResponse & {
  quackLatencyMs: number
}

async function fetchCatalogueStatus(): Promise<CatalogueStatus> {
  const startedAt = performance.now()
  const status = await readQuackCatalogueStatus()

  return {
    ...status,
    quackLatencyMs: performance.now() - startedAt,
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
