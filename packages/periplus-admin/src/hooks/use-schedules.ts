import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { RequestDefinition, RequestSchedule } from "@/types/schedules"
async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json()
}
export function useDefinitions() {
  return useQuery({
    queryKey: ["request-definitions"],
    queryFn: ({ signal }) =>
      read<RequestDefinition[]>("/request-definitions", signal),
    retry: false,
    refetchInterval: 10000,
  })
}
export function useSchedules() {
  return useQuery({
    queryKey: ["request-schedules"],
    queryFn: ({ signal }) =>
      read<RequestSchedule[]>("/request-definitions/schedules", signal),
    retry: false,
    refetchInterval: 5000,
  })
}
export function useScheduleAction() {
  const client = useQueryClient()
  return useMutation({
    retry: false,
    mutationFn: async ({
      path,
      method = "POST",
      body,
    }: {
      path: string
      method?: string
      body?: unknown
    }) => {
      const response = await fetch(apiUrl(path), {
        method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<{ id?: string; request_id?: string }>
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["request-definitions"] })
      void client.invalidateQueries({ queryKey: ["request-schedules"] })
      toast.success("Saved successfully.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
