import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { runQuackCatalogueQuery } from "@/components/catalogue/workbench-query-runtime"
import type {
  CataloguePreparedSql,
  CatalogueQueryRequest,
  CatalogueQueryResult,
  CatalogueStatementKind,
} from "@/types/catalogue"

const statementKinds = new Set<CatalogueStatementKind>([
  "query",
  "explain",
  "explain_analyze",
])

async function runCatalogueQuery(
  request: CatalogueQueryRequest
): Promise<CatalogueQueryResult> {
  const response = await fetch(apiUrl("/catalogue/sql/prepare"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  const prepared = (await response.json()) as CataloguePreparedSql
  if (!statementKinds.has(prepared.statement_kind)) {
    throw new Error(
      "Catalogue SQL preparation returned an unknown statement kind."
    )
  }
  return runQuackCatalogueQuery(prepared.sql, prepared.statement_kind)
}

export { formatArrowValue } from "@/lib/catalogue-arrow"

export function useCatalogueQuery() {
  return useMutation({
    mutationFn: runCatalogueQuery,
    onError: (error) => toast.error(extractApiError(error)),
  })
}
