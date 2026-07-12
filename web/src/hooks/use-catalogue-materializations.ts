import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { CatalogueMaterializationList, CatalogueMaterializationRecord } from "@/types/catalogue"

const key = ["catalogue-materializations"] as const

function invalidateDefinitionSummaries(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ["saved-queries"] })
  void client.invalidateQueries({ queryKey: ["saved-query"] })
  void client.invalidateQueries({ queryKey: ["catalogue-views"] })
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export type CreateCatalogueMaterializationInput = {
  source:
    | { kind: "query"; query_id: string; active_query_revision_id?: string }
    | { kind: "view"; view_reference_id: string }
  name: string
  display_name?: string
  description?: string
  refresh_mode: "full" | "scope_incremental"
  scope_kind?: "document"
  scope_column?: string
  live_enabled?: boolean
  backfill_enabled?: boolean
  backfill_scopes_per_minute?: number
  partition_column?: string
}

export function useCatalogueMaterializations() {
  return useQuery({
    queryKey: key,
    queryFn: () => json<CatalogueMaterializationList>("/catalogue/materializations/"),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.status === "dematerializing" || item.status === "backfilling")
        ? 2_000
        : false,
  })
}

export function useCatalogueMaterialization(id: string | null | undefined) {
  return useQuery({
    queryKey: [...key, id],
    queryFn: () => json<CatalogueMaterializationRecord>(`/catalogue/materializations/${id}`),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      query.state.data?.status === "dematerializing" || query.state.data?.status === "backfilling"
        ? 2_000
        : false,
  })
}

export function useCreateCatalogueMaterialization() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ source, ...input }: CreateCatalogueMaterializationInput) =>
      json<CatalogueMaterializationRecord>(
        source.kind === "query"
          ? `/catalogue/queries/${source.query_id}/materialization`
          : `/catalogue/views/${source.view_reference_id}/materialization`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...input,
          ...(source.kind === "query"
            ? { active_query_revision_id: source.active_query_revision_id }
            : {}),
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      invalidateDefinitionSummaries(client)
      toast.success("Durable data created.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useRebuildCatalogueMaterialization() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ materialization, targetQueryRevisionId }: { materialization: CatalogueMaterializationRecord; targetQueryRevisionId?: string }) =>
      json<CatalogueMaterializationRecord>(`/catalogue/materializations/${materialization.id}/rebuild`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_ducklake_table_uuid: materialization.ducklake_table_uuid,
          target_query_revision_id: targetQueryRevisionId,
        }),
      }),
    onSuccess: (materialization) => {
      void client.invalidateQueries({ queryKey: key })
      invalidateDefinitionSummaries(client)
      toast.success(
        materialization.refresh_mode === "scope_incremental"
          ? "Durable data rebuild started."
          : "Durable data rebuilt."
      )
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateCatalogueMaterializationMaintenance() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: { id: string; live_enabled?: boolean; backfill_enabled?: boolean; backfill_scopes_per_minute?: number }) =>
      json<CatalogueMaterializationRecord>(`/catalogue/materializations/${id}/maintenance`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    onSuccess: (materialization) => {
      void client.invalidateQueries({ queryKey: key })
      invalidateDefinitionSummaries(client)
      toast.success(materialization.live_enabled ? "Live maintenance resumed." : "Maintenance updated.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDematerialize() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (materialization: CatalogueMaterializationRecord) => {
      const params = new URLSearchParams({
        expected_ducklake_table_uuid: materialization.ducklake_table_uuid,
      })
      return json<CatalogueMaterializationRecord>(`/catalogue/materializations/${materialization.id}?${params}`, {
        method: "DELETE",
      })
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key })
      invalidateDefinitionSummaries(client)
      toast.success("Dematerialization requested. New work has been stopped.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
