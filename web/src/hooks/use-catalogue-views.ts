import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { CatalogueViewList, CatalogueViewRecord } from "@/types/catalogue"

const key = ["catalogue-views"] as const

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return (await response.json()) as T
}

export function useCatalogueViews() {
  return useQuery({
    queryKey: key,
    queryFn: () => json<CatalogueViewList>("/catalogue/views/"),
  })
}

export function useCreateCatalogueView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string; display_name?: string; description?: string; sql: string }) =>
      json<CatalogueViewRecord>("/catalogue/views/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("View created.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useAdoptCatalogueView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (view: CatalogueViewRecord) =>
      json<CatalogueViewRecord>("/catalogue/views/adopt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ducklake_view_uuid: view.ducklake_view_uuid }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("View adopted.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateCatalogueView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { view: CatalogueViewRecord; sql: string; display_name: string; description: string }) =>
      json<CatalogueViewRecord>(`/catalogue/views/${input.view.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_ducklake_view_uuid: input.view.ducklake_view_uuid,
          sql: input.sql,
          display_name: input.display_name,
          description: input.description || null,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("View updated.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDetachCatalogueView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (view: CatalogueViewRecord) => {
      const response = await fetch(apiUrl(`/catalogue/views/${view.id}/reference`), { method: "DELETE" })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("View detached from Atlas.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDropCatalogueView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (view: CatalogueViewRecord) => {
      const query = new URLSearchParams({ expected_ducklake_view_uuid: view.ducklake_view_uuid })
      const response = await fetch(apiUrl(`/catalogue/views/${view.id}/object?${query}`), { method: "DELETE" })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("View dropped.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
