"use client"

import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { responseJson } from "@/lib/api"
import { CaptureBuffer, type CaptureBufferSnapshot } from "@/lib/capture-buffer"
import type { CapturePage, CaptureBatch } from "@/types/live"

export function useCaptureStream(enabled: boolean, playing: boolean) {
  const cache = useQueryClient()
  const [buffer] = useState(() => new CaptureBuffer())
  const lastBatch = useRef<CaptureBatch | null>(null)
  const [presentation, setPresentation] = useState<CaptureBufferSnapshot>(() => buffer.snapshot())
  const query = useQuery({
    queryKey: ["crawler-capture-feed"],
    enabled: enabled && playing,
    queryFn: async ({ signal }) => {
      const previous = cache.getQueryData<CaptureBatch>(["crawler-capture-feed"])
      let cursor = lastBatch.current?.cursor ?? previous?.cursor
      let combined: CapturePage | undefined
      // Bound one read to 600 captures. A remaining page is fetched promptly,
      // without repeating expensive velocity/catalogue reads.
      for (let page = 0; page < 3; page++) {
        const response = await fetch(`/api/frontier/captures${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, {
          signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache: "no-store",
        })
        const next = await responseJson<CapturePage>(response)
        combined = combined && !next.bootstrap ? {...next, items: [...combined.items, ...next.items]} : next
        cursor = next.cursor
        if (!next.has_more) break
      }
      const tail = new Map((combined!.bootstrap ? [] : previous?.tail ?? []).map(item => [item.observation_id, item]))
      for (const item of combined!.items) tail.set(item.observation_id, item)
      return {...combined!, tail: [...tail.values()].sort((a,b) => Date.parse(a.completed_at) - Date.parse(b.completed_at) || a.observation_id.localeCompare(b.observation_id)).slice(-7)}
    },
    refetchInterval: query => query.state.data?.has_more ? 100 : 5000,
    staleTime: 0,
    retry: false,
  })
  useEffect(() => {
    if (!enabled || !playing || !query.data || lastBatch.current === query.data) return
    buffer.accept(lastBatch.current === null ? query.data.tail : query.data.items, performance.now(), query.data.bootstrap)
    lastBatch.current = query.data
    // Defer only presentation; React Query owns all network state and retries.
    const timer = window.setTimeout(() => setPresentation(buffer.snapshot()), 0)
    return () => window.clearTimeout(timer)
  }, [buffer, enabled, playing, query.data])
  useEffect(() => {
    if (!enabled || !playing) return
    const timer = window.setInterval(() => {
      const next = buffer.tick(performance.now(), window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden)
      if (next) setPresentation(next)
    }, 100)
    return () => window.clearInterval(timer)
  }, [buffer, enabled, playing])
  return { ...presentation, query }
}
