import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CatalogueScalarMacroList,
  CatalogueScalarMacroRecord,
} from "@/types/catalogue"

const key = ["catalogue-scalar-macros"] as const

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return (await response.json()) as T
}

export function useCatalogueScalarMacros() {
  return useQuery({
    queryKey: key,
    queryFn: () =>
      json<CatalogueScalarMacroList>("/catalogue/scalar-macros/"),
  })
}

export function useCreateCatalogueScalarMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      slug: string
      description?: string
      parameters: string[]
      sql: string
    }) =>
      json<CatalogueScalarMacroRecord>("/catalogue/scalar-macros/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Scalar macro created.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateCatalogueScalarMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      macro: CatalogueScalarMacroRecord
      slug: string
      description: string
      parameters: string[]
      sql: string
    }) =>
      json<CatalogueScalarMacroRecord>(
        `/catalogue/scalar-macros/${input.macro.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_definition_revision_id:
              input.macro.definition_revision_id,
            slug: input.slug,
            description: input.description || null,
            parameters: input.parameters,
            sql: input.sql,
          }),
        }
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Scalar macro updated.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDropCatalogueScalarMacro() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (macro: CatalogueScalarMacroRecord) => {
      const query = new URLSearchParams({
        expected_definition_revision_id: macro.definition_revision_id,
      })
      const response = await fetch(
        apiUrl(`/catalogue/scalar-macros/${macro.id}?${query}`),
        { method: "DELETE" }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Scalar macro deleted.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
