import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CatalogueTableMacroList,
  CatalogueTableMacroRecord,
} from "@/types/catalogue"

const key = ["catalogue-table-macros"] as const

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return (await response.json()) as T
}

export function useCatalogueTableMacros() {
  return useQuery({
    queryKey: key,
    queryFn: () => json<CatalogueTableMacroList>("/catalogue/macros/"),
  })
}

export function useCreateCatalogueTableMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      slug: string
      description?: string
      parameters: string[]
      sql: string
      created_from_query_revision_id?: string
    }) =>
      json<CatalogueTableMacroRecord>("/catalogue/macros/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Table macro created.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateCatalogueTableMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      macro: CatalogueTableMacroRecord
      slug: string
      description: string
      parameters: string[]
      sql: string
    }) =>
      json<CatalogueTableMacroRecord>(`/catalogue/macros/${input.macro.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_definition_revision_id: input.macro.definition_revision_id,
          slug: input.slug,
          description: input.description || null,
          parameters: input.parameters,
          sql: input.sql,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Table macro updated.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDropCatalogueTableMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (macro: CatalogueTableMacroRecord) => {
      const query = new URLSearchParams({
        expected_definition_revision_id: macro.definition_revision_id,
      })
      const response = await fetch(
        apiUrl(`/catalogue/macros/${macro.id}?${query}`),
        { method: "DELETE" }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Table macro deleted.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
