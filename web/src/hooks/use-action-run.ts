import { useMutation } from "@tanstack/react-query"
import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type { ProgressEvent } from "@/types/progress"
import type { TaskProgressEnvelope, TaskRunRecord, TaskRunSubmission } from "@/types/tasks"

const terminalStatuses = new Set(["succeeded", "failed", "cancelled", "skipped"])

async function waitForDurableTerminal(runId: string, signal: AbortSignal) {
  while (true) {
    const response = await fetch(apiUrl(`/task-runs/${runId}`), { signal })
    if (!response.ok) throw await apiErrorFromResponse(response)
    const run = (await response.json()) as TaskRunRecord
    if (terminalStatuses.has(run.status)) return run
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
  }
}

export function useActionRun<TInput, TResult>(path: string, label: string, emptyResult: TResult) {
  const abortControllerRef = useRef<AbortController | null>(null)
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [result, setResult] = useState<TResult>(emptyResult)
  const [runId, setRunId] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async (input: TInput) => {
      const abortController = new AbortController()
      abortControllerRef.current = abortController
      const submissionResponse = await fetch(apiUrl(path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal: abortController.signal,
      })
      if (!submissionResponse.ok) throw await apiErrorFromResponse(submissionResponse)
      const submission = (await submissionResponse.json()) as TaskRunSubmission
      setRunId(submission.run_id)

      let terminalError: string | null = null
      try {
        const progressResponse = await fetch(apiUrl(`/task-runs/${submission.run_id}/progress`), {
          headers: { Accept: "text/event-stream" },
          signal: abortController.signal,
        })
        if (!progressResponse.ok) throw await apiErrorFromResponse(progressResponse)
        await readSseStream(progressResponse, (message) => {
          if (message.event === "error") return
          const envelope = JSON.parse(message.data) as TaskProgressEnvelope
          if (message.event === "progress") {
            setEvents((current) => [...current, envelope.data as ProgressEvent])
          } else if (["failed", "cancelled", "skipped"].includes(message.event)) {
            terminalError = (envelope.data as { error?: string }).error ?? `Task run ${message.event}.`
          }
        })
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") throw error
      }
      const durableRun = await waitForDurableTerminal(submission.run_id, abortController.signal)
      if (durableRun.status !== "succeeded") {
        terminalError = durableRun.error ?? `Task run ${durableRun.status}.`
      }
      if (terminalError) throw new Error(terminalError)

      const resultResponse = await fetch(apiUrl(`/task-runs/${submission.run_id}/result`), {
        signal: abortController.signal,
      })
      if (!resultResponse.ok) throw await apiErrorFromResponse(resultResponse)
      const taskResult = (await resultResponse.json()) as TResult
      setResult(taskResult)
      return taskResult
    },
    onMutate: () => {
      setEvents([])
      setResult(emptyResult)
      setRunId(null)
    },
    onError: (error) => {
      if (error instanceof DOMException && error.name === "AbortError") {
        toast.error(`${label} progress disconnected.`)
        return
      }
      toast.error(extractApiError(error))
    },
    onSettled: () => {
      abortControllerRef.current = null
    },
  })

  const cancel = useCallback(async () => {
    if (!runId) {
      abortControllerRef.current?.abort()
      return
    }
    try {
      const response = await fetch(apiUrl(`/task-runs/${runId}/cancel`), { method: "POST" })
      if (!response.ok) throw await apiErrorFromResponse(response)
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }, [runId])
  return {
    cancel,
    events,
    result,
    runId,
    run: mutation.mutate,
    isRunning: mutation.isPending,
    isSuccess: mutation.isSuccess,
    error: mutation.error,
    reset: mutation.reset,
  }
}
