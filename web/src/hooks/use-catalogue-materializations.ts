import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CatalogueMaterializationList,
  CatalogueMaterializationRecord,
} from "@/types/catalogue"

const key = ["catalogue-materializations"] as const

function invalidate(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: key })
  void client.invalidateQueries({ queryKey: ["catalogue-views"] })
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export type CreateCatalogueMaterializationInput = {
  view_reference_id: string
  name: string
  display_name?: string
  description?: string
  source_table: string
  refresh_strategy: "keyed" | "append" | "full"
  key_columns: string[]
  scope_relations?: Record<string, string[]>
  refresh_delay_seconds?: number
  partition_column?: string
}

export function useCatalogueMaterializations() {
  return useQuery({
    queryKey: key,
    queryFn: () =>
      json<CatalogueMaterializationList>("/catalogue/materializations/"),
  })
}

export function useCatalogueMaterialization(id: string | null | undefined) {
  return useQuery({
    queryKey: [...key, id],
    queryFn: () =>
      json<CatalogueMaterializationRecord>(`/catalogue/materializations/${id}`),
    enabled: Boolean(id),
    refetchInterval: 2000,
  })
}

export function useCreateCatalogueMaterialization() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      view_reference_id,
      ...input
    }: CreateCatalogueMaterializationInput) =>
      json<CatalogueMaterializationRecord>(
        `/catalogue/views/${view_reference_id}/materialization`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        }
      ),
    onSuccess: () => {
      invalidate(client)
      toast.success("Materialization requested.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateCatalogueMaterialization() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      ...input
    }: {
      id: string
      desired_state?: "live" | "paused"
      refresh_delay_seconds?: number
    }) =>
      json<CatalogueMaterializationRecord>(
        `/catalogue/materializations/${id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        }
      ),
    onSuccess: (materialization) => {
      invalidate(client)
      toast.success(
        materialization.desired_state === "live"
          ? "Materialization resumed."
          : "Materialization updated."
      )
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDematerialize() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (materialization: CatalogueMaterializationRecord) =>
      json<CatalogueMaterializationRecord>(
        `/catalogue/materializations/${materialization.id}`,
        { method: "DELETE" }
      ),
    onSuccess: () => {
      invalidate(client)
      toast.success("Dematerialization requested.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
