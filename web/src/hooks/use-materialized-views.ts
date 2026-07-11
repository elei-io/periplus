import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { MaterializedViewList, MaterializedViewRecord } from "@/types/catalogue"

const key = ["materialized-views"] as const

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export type CreateMaterializedViewInput = {
  name: string
  display_name?: string
  description?: string
  query_revision_id?: string
  source_view_uuid?: string
  refresh_mode: "full" | "scope_incremental"
  scope_kind?: "document"
  scope_column?: string
  live_enabled?: boolean
  backfill_enabled?: boolean
  backfill_scopes_per_minute?: number
  partition_column?: string
}

export function useMaterializedViews() {
  return useQuery({
    queryKey: key,
    queryFn: () => json<MaterializedViewList>("/materialized-views/"),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.status === "deleting" || item.status === "backfilling")
        ? 2_000
        : false,
  })
}

export function useCreateMaterializedView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateMaterializedViewInput) =>
      json<MaterializedViewRecord>("/materialized-views/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Materialized view created.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useRefreshMaterializedView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (view: MaterializedViewRecord) =>
      json<MaterializedViewRecord>(`/materialized-views/${view.id}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_ducklake_table_uuid: view.ducklake_table_uuid }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Materialized view refreshed.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateMaterializedViewMaintenance() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: { id: string; live_enabled?: boolean; backfill_enabled?: boolean; backfill_scopes_per_minute?: number }) =>
      json<MaterializedViewRecord>(`/materialized-views/${id}/maintenance`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: (view) => {
      void client.invalidateQueries({ queryKey: key })
      toast.success(view.live_enabled ? "Live maintenance resumed." : "Maintenance updated.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDropMaterializedView() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (view: MaterializedViewRecord) => {
      const params = new URLSearchParams({
        expected_ducklake_table_uuid: view.ducklake_table_uuid,
      })
      return json<MaterializedViewRecord>(`/materialized-views/${view.id}?${params}`, {
        method: "DELETE",
      })
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      toast.success("Deletion requested. New work has been stopped.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
