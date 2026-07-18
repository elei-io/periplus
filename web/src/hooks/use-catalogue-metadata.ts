import { useQuery } from "@tanstack/react-query"

import { readQuackCatalogueMetadata } from "@/components/catalogue/workbench-query-runtime"

export function useCatalogueMetadata() {
  return useQuery({
    queryKey: ["catalogue-metadata"],
    queryFn: readQuackCatalogueMetadata,
    refetchInterval: 60_000,
  })
}
