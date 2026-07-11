import { useMutation } from "@tanstack/react-query"
import { tableFromIPC } from "apache-arrow"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { CatalogueQueryResult } from "@/types/catalogue"

async function runCatalogueQuery(sql: string): Promise<CatalogueQueryResult> {
  const response = await fetch(apiUrl("/catalogue/sql"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  const table = tableFromIPC(await response.arrayBuffer())
  const columns = table.schema.fields.map((field) => field.name)
  const rows: unknown[][] = []
  for (const row of table) {
    rows.push(columns.map((column) => row[column]))
  }
  return { columns, rows }
}

export function useCatalogueQuery() {
  return useMutation({
    mutationFn: runCatalogueQuery,
    onError: (error) => toast.error(extractApiError(error)),
  })
}
