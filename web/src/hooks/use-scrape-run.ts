import { useMutation } from "@tanstack/react-query"
import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readScrapeStream } from "@/lib/scrape-stream"
import type { CrawlProgressEvent } from "@/types/index"
import type { ScrapeInput, ScrapeOutput } from "@/types/scrape"

export function useScrapeRun() {
  const abortControllerRef = useRef<AbortController | null>(null)
  const [events, setEvents] = useState<CrawlProgressEvent[]>([])
  const [result, setResult] = useState<ScrapeOutput | null>(null)

  const mutation = useMutation({
    mutationFn: async (input: ScrapeInput) => {
      const abortController = new AbortController()
      abortControllerRef.current = abortController
      let latestResult: ScrapeOutput | null = null

      const response = await fetch(apiUrl("/scrape/"), {
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

      await readScrapeStream(response, (event) => {
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
      setResult(null)
    },
    onError: (error) => {
      if (error instanceof DOMException && error.name === "AbortError") {
        toast.error("Scrape run cancelled.")
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
