import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { SavedQueryDetail, SavedQueryList } from "@/types/catalogue"

const listKey = ["saved-queries"] as const

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return (await response.json()) as T
}

export function useSavedQueries() {
  return useQuery({ queryKey: listKey, queryFn: () => json<SavedQueryList>("/catalogue/queries/") })
}

export function useSavedQuery(id: string | null) {
  return useQuery({
    queryKey: ["saved-query", id],
    queryFn: () => json<SavedQueryDetail>(`/catalogue/queries/${id}`),
    enabled: Boolean(id),
  })
}

export function useCreateSavedQuery() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string; description?: string; sql: string; change_note?: string }) =>
      json<SavedQueryDetail>("/catalogue/queries/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: (query) => {
      void client.invalidateQueries({ queryKey: listKey })
      client.setQueryData(["saved-query", query.id], query)
      toast.success("Query saved.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateSavedQuery() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { query: SavedQueryDetail; sql: string; name?: string; description?: string; change_note?: string }) =>
      json<SavedQueryDetail>(`/catalogue/queries/${input.query.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_current_revision_id: input.query.current_revision_id,
          sql: input.sql,
          name: input.name,
          description: input.description ?? null,
          change_note: input.change_note || null,
        }),
      }),
    onSuccess: (query) => {
      void client.invalidateQueries({ queryKey: listKey })
      client.setQueryData(["saved-query", query.id], query)
      toast.success(`Revision ${query.current_revision} saved.`)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useRestoreSavedQueryRevision() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { query: SavedQueryDetail; revisionId: string }) =>
      json<SavedQueryDetail>(`/catalogue/queries/${input.query.id}/revisions/${input.revisionId}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_current_revision_id: input.query.current_revision_id }),
      }),
    onSuccess: (query) => {
      void client.invalidateQueries({ queryKey: listKey })
      client.setQueryData(["saved-query", query.id], query)
      toast.success(`Restored as revision ${query.current_revision}.`)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useArchiveSavedQuery() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await fetch(apiUrl(`/catalogue/queries/${id}`), { method: "DELETE" })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: listKey })
      toast.success("Query archived.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
