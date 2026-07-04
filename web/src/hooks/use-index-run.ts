import { useMutation } from "@tanstack/react-query"
import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readIndexStream } from "@/lib/index-stream"
import type { CrawlProgressEvent, IndexInput, IndexLink } from "@/types/index"

export function useIndexRun() {
  const abortControllerRef = useRef<AbortController | null>(null)
  const [events, setEvents] = useState<CrawlProgressEvent[]>([])
  const [result, setResult] = useState<IndexLink[]>([])

  const mutation = useMutation({
    mutationFn: async (input: IndexInput) => {
      const abortController = new AbortController()
      abortControllerRef.current = abortController
      let latestResult: IndexLink[] = []

      const response = await fetch(apiUrl("/index/"), {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(input),
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }

      await readIndexStream(response, (event) => {
        if (event.type === "progress") {
          setEvents((currentEvents) => [...currentEvents, event.data])
          return
        }

        if (event.type === "result") {
          latestResult = event.data
          setResult(event.data)
          return
        }

        if (event.type === "error") {
          throw new Error(event.data.message)
        }
      })

      return latestResult
    },
    onMutate: () => {
      setEvents([])
      setResult([])
    },
    onError: (error) => {
      if (error instanceof DOMException && error.name === "AbortError") {
        toast.error("Index run cancelled.")
        return
      }

      toast.error(extractApiError(error))
    },
    onSettled: () => {
      abortControllerRef.current = null
    },
  })

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return {
    cancel,
    events,
    result,
    run: mutation.mutate,
    isRunning: mutation.isPending,
    isSuccess: mutation.isSuccess,
    error: mutation.error,
    reset: mutation.reset,
  }
}
