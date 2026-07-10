import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type { SearchInput } from "@/types/search"
import type { TaskRunRecord, TaskRunSubmission } from "@/types/tasks"

const searchRunsKey = ["task-runs", "search"] as const

export function useSearchRuns() {
  return useQuery({
    queryKey: searchRunsKey,
    queryFn: async () => {
      const response = await fetch(
        apiUrl("/task-runs/?primitive=search&terminal_limit=20")
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TaskRunRecord[]
    },
    refetchInterval: 2_000,
  })
}

export function useSubmitSearch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: SearchInput) => {
      const response = await fetch(apiUrl("/search/"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TaskRunSubmission
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: searchRunsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCancelSearchRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (runId: string) => {
      const response = await fetch(apiUrl(`/task-runs/${runId}/cancel`), {
        method: "POST",
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TaskRunRecord
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: searchRunsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
