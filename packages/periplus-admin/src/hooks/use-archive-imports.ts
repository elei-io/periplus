import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { ArchiveImport, CreateArchiveImport } from "@/types/archive-imports"

const path = "/operations/archive-imports"
export function useArchiveImports() {
  return useQuery({ queryKey: ["archive-imports"], refetchInterval: 3000, retry: false,
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl(path), { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return await response.json() as ArchiveImport[]
    } })
}
export function useArchiveImportAction() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: { create: CreateArchiveImport } | { id: string; action: "cancel" | "retry" }) => {
      const response = await fetch(apiUrl("create" in input ? path : `${path}/${input.id}/${input.action}`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: "create" in input ? JSON.stringify(input.create) : undefined,
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return await response.json() as ArchiveImport
    },
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["archive-imports"] }) },
    onError: error => toast.error(extractApiError(error)),
  })
}
