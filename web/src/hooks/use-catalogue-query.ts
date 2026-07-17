import { useMutation } from "@tanstack/react-query"
import { tableFromIPC } from "apache-arrow"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
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
  const response = await fetch(apiUrl("/catalogue/sql"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }
  const statementKind = response.headers.get("X-Atlas-Statement-Kind")
  if (!statementKinds.has(statementKind as CatalogueStatementKind)) {
    throw new Error("Catalogue response did not identify its statement kind.")
  }

  const table = tableFromIPC(await response.arrayBuffer())
  const columns = table.schema.fields.map((field) => field.name)
  const columnTypes = table.schema.fields.map((field) => String(field.type))
  const vectors = table.schema.fields.map((field, index) => ({
    type: String(field.type),
    vector: table.getChildAt(index),
  }))
  const rows = Array.from({ length: table.numRows }, (_, rowIndex) =>
    vectors.map(({ type, vector }) =>
      formatArrowValue(type, vector?.get(rowIndex))
    )
  )
  return {
    statementKind: statementKind as CatalogueStatementKind,
    columns,
    columnTypes,
    rows,
  }
}

export function formatArrowValue(type: string, value: unknown): unknown {
  if (value === null || value === undefined || !type.startsWith("Timestamp<")) {
    return value
  }
  if (value instanceof Date) return value.toISOString()
  if (typeof value === "number") return new Date(value).toISOString()
  if (typeof value === "bigint") {
    const unit = type.slice("Timestamp<".length).split(",", 1)[0]
    const milliseconds =
      unit === "SECOND"
        ? value * 1_000n
        : unit === "MILLISECOND"
          ? value
          : unit === "MICROSECOND"
            ? value / 1_000n
            : value / 1_000_000n
    return new Date(Number(milliseconds)).toISOString()
  }
  return value
}

export function useCatalogueQuery() {
  return useMutation({
    mutationFn: runCatalogueQuery,
    onError: (error) => toast.error(extractApiError(error)),
  })
}
