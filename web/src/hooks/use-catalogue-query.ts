import { useMutation } from "@tanstack/react-query"
import { RecordBatchReader } from "apache-arrow"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { formatArrowValue } from "@/lib/catalogue-arrow"
import type {
  CatalogueQueryRequest,
  CatalogueQueryResult,
  CatalogueQueryState,
  CatalogueStatementKind,
} from "@/types/catalogue"

const statementKinds = new Set<CatalogueStatementKind>([
  "query",
  "explain",
  "explain_analyze",
])
const terminalQueryStatuses = new Set<CatalogueQueryState["status"]>([
  "succeeded",
  "failed",
  "cancelled",
])
const terminalStatePollMilliseconds = 100
const terminalStateMaximumAttempts = 50

async function waitForTerminalQueryState(
  queryId: string
): Promise<CatalogueQueryState> {
  for (let attempt = 0; attempt < terminalStateMaximumAttempts; attempt += 1) {
    const response = await fetch(
      apiUrl(`/catalogue/query-executions/${queryId}`)
    )
    if (!response.ok) throw await apiErrorFromResponse(response)
    const state = (await response.json()) as CatalogueQueryState
    if (terminalQueryStatuses.has(state.status)) return state
    await new Promise((resolve) =>
      window.setTimeout(resolve, terminalStatePollMilliseconds)
    )
  }
  throw new Error("Catalogue query status did not settle after streaming.")
}

async function runCatalogueQuery(
  request: CatalogueQueryRequest
): Promise<CatalogueQueryResult> {
  const response = await fetch(apiUrl("/catalogue/query-executions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)

  const queryId = response.headers.get("X-Atlas-Query-ID")
  const statementKind = response.headers.get(
    "X-Atlas-Statement-Kind"
  ) as CatalogueStatementKind | null
  if (!queryId || !statementKind || !statementKinds.has(statementKind)) {
    throw new Error(
      "Catalogue query response is missing its execution metadata."
    )
  }

  let columns: string[] = []
  let columnTypes: string[] = []
  const rows: unknown[][] = []
  let streamError: unknown = null
  try {
    const reader = await RecordBatchReader.from(response)
    await reader.open()
    columns = reader.schema.fields.map((field) => field.name)
    columnTypes = reader.schema.fields.map((field) => String(field.type))
    for await (const batch of reader) {
      const vectors = batch.schema.fields.map((field, index) => ({
        type: String(field.type),
        vector: batch.getChildAt(index),
      }))
      for (let rowIndex = 0; rowIndex < batch.numRows; rowIndex += 1) {
        rows.push(
          vectors.map(({ type, vector }) =>
            formatArrowValue(type, vector?.get(rowIndex))
          )
        )
      }
    }
  } catch (error) {
    streamError = error
  }

  const state = await waitForTerminalQueryState(queryId)
  if (state.status !== "succeeded") {
    throw new Error(state.error ?? `Catalogue query ${state.status}.`)
  }
  if (streamError) throw streamError

  return { statementKind, columns, columnTypes, rows }
}

export { formatArrowValue }

export function useCatalogueQuery() {
  return useMutation({
    mutationFn: runCatalogueQuery,
    onError: (error) => toast.error(extractApiError(error)),
  })
}
