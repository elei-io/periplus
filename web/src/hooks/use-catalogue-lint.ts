import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type { CatalogueLintResult, CatalogueQueryMode } from "@/types/catalogue"

const lintDebounceMs = 700

async function lintCatalogueQuery(
  sql: string,
  mode: CatalogueQueryMode,
  signal: AbortSignal
): Promise<CatalogueLintResult> {
  const response = await fetch(apiUrl("/catalogue/sql/lint"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql, mode }),
    signal,
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }
  return (await response.json()) as CatalogueLintResult
}

export function useCatalogueLint(
  sql: string,
  mode: CatalogueQueryMode,
  enabled = true
) {
  const [debouncedSql, setDebouncedSql] = useState("")

  useEffect(() => {
    const timeout = window.setTimeout(
      () => setDebouncedSql(enabled ? sql : ""),
      lintDebounceMs
    )
    return () => window.clearTimeout(timeout)
  }, [enabled, sql])

  const query = useQuery({
    queryKey: ["catalogue-sql-lint", debouncedSql, mode],
    queryFn: ({ signal }) => lintCatalogueQuery(debouncedSql, mode, signal),
    enabled: enabled && Boolean(debouncedSql.trim()),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })

  return {
    ...query,
    data: enabled && debouncedSql === sql ? query.data : undefined,
    isDebouncing: enabled && debouncedSql !== sql,
  }
}
