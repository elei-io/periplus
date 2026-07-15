import { useMutation } from "@tanstack/react-query"
import { tableFromIPC } from "apache-arrow"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CatalogueQueryRequest,
  CatalogueQueryResult,
} from "@/types/catalogue"

async function runCatalogueQuery(
  request: CatalogueQueryRequest
): Promise<CatalogueQueryResult> {
  const response = await fetch(apiUrl("/catalogue/sql"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  const table = tableFromIPC(await response.arrayBuffer())
  const columns = table.schema.fields.map((field) => field.name)
  const columnTypes = table.schema.fields.map((field) => String(field.type))
  const vectors = columns.map((_, index) => table.getChildAt(index))
  const rows = Array.from({ length: table.numRows }, (_, rowIndex) =>
    vectors.map((vector) => vector?.get(rowIndex))
  )
  return { columns, columnTypes, rows }
}

export function useCatalogueQuery() {
  return useMutation({
    mutationFn: runCatalogueQuery,
    onError: (error) => toast.error(extractApiError(error)),
  })
}
