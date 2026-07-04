import { useMutation } from "@tanstack/react-query"
import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readSearchStream } from "@/lib/search-stream"
import type { CrawlProgressEvent } from "@/types/index"
import type { SearchInput, SearchResult } from "@/types/search"

export function useSearchRun() {
  const abortControllerRef = useRef<AbortController | null>(null)
  const [events, setEvents] = useState<CrawlProgressEvent[]>([])
  const [result, setResult] = useState<SearchResult[]>([])

  const mutation = useMutation({
    mutationFn: async (input: SearchInput) => {
      const abortController = new AbortController()
      abortControllerRef.current = abortController
      let latestResult: SearchResult[] = []
      const params = new URLSearchParams({
        query: input.query,
        max_results: String(input.max_results),
      })

      const response = await fetch(apiUrl(`/search/?${params.toString()}`), {
        method: "GET",
        headers: {
          Accept: "text/event-stream",
        },
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }

      await readSearchStream(response, (event) => {
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
        toast.error("Search cancelled.")
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
