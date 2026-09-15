import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { BuildAction, MaterialBuild } from "@/types/materialization"
const path = "/operations/materializations/runs"
export function useMaterializationRuns() {
  return useQuery({ queryKey: ["materialization-runs"], queryFn: async ({ signal }) => {
    const response = await fetch(apiUrl(path), { signal })
    if (!response.ok) throw await apiErrorFromResponse(response)
    return await response.json() as MaterialBuild[]
  }, refetchInterval: 3000, retry: false })
}
export function useBuildAction() {
  const client = useQueryClient()
  return useMutation({ mutationFn: async (input: { id: string; action: BuildAction } | { page_size: number }) => {
    const response = await fetch(apiUrl("id" in input ? `${path}/${input.id}/actions` : path), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify("id" in input ? { action: input.action } : input),
    })
    if (!response.ok) throw await apiErrorFromResponse(response)
  }, onSuccess: () => { void client.invalidateQueries({ queryKey: ["materialization-runs"] }) },
  onError: (error) => toast.error(extractApiError(error)) })
}
