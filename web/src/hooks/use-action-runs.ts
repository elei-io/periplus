import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type { ProgressEvent } from "@/types/progress"
import type {
  TaskPrimitive,
  TaskProgressEnvelope,
  TaskRunRecord,
  TaskRunSubmission,
} from "@/types/tasks"

function taskRunsKey(primitive: TaskPrimitive) {
  return ["task-runs", primitive] as const
}

export function useActionRuns(primitive: TaskPrimitive) {
  return useQuery({
    queryKey: taskRunsKey(primitive),
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/task-runs/?primitive=${primitive}&terminal_limit=20`)
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TaskRunRecord[]
    },
    refetchInterval: (query) =>
      query.state.data?.some((run) =>
        ["queued", "running"].includes(run.status)
      )
        ? 2_000
        : 10_000,
  })
}

export function useSubmitAction<TInput>(
  primitive: TaskPrimitive,
  path: string
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: TInput) => {
      const response = await fetch(apiUrl(path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TaskRunSubmission
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: taskRunsKey(primitive) })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCancelActionRun(primitive: TaskPrimitive) {
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
      await queryClient.invalidateQueries({ queryKey: taskRunsKey(primitive) })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useTaskRunProgress(
  primitive: TaskPrimitive,
  runId: string,
  active: boolean
) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const seenEventIds = useRef(new Set<string>())
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!active) return undefined
    const controller = new AbortController()

    async function connect() {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(apiUrl(`/task-runs/${runId}/progress`), {
            headers: { Accept: "text/event-stream" },
            signal: controller.signal,
          })
          if (!response.ok) throw await apiErrorFromResponse(response)
          await readSseStream(response, (message) => {
            const envelope = JSON.parse(message.data) as TaskProgressEnvelope
            if (
              message.event === "progress" &&
              !seenEventIds.current.has(envelope.event_id)
            ) {
              seenEventIds.current.add(envelope.event_id)
              setEvents((current) => [
                ...current,
                envelope.data as ProgressEvent,
              ])
            }
            if (
              ["succeeded", "failed", "cancelled", "skipped"].includes(
                message.event
              )
            ) {
              void queryClient.invalidateQueries({
                queryKey: taskRunsKey(primitive),
              })
            }
          })
        } catch {
          if (controller.signal.aborted) return
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2_000))
      }
    }

    void connect()
    return () => controller.abort()
  }, [active, primitive, queryClient, runId])

  return events
}

export function useTaskRunResult<TResult>(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["task-run-result", runId],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/task-runs/${runId}/result`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as TResult
    },
    enabled,
  })
}
