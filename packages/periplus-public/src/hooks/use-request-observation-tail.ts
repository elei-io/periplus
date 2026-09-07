"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { responseJson } from "@/lib/api"
import { RequestTailBuffer } from "@/lib/request-tail-buffer"
import type { CollectionArrivalsPage } from "@/types/frontier-items"

export function useRequestObservationTail(id: string, playing: boolean) {
  const [buffer] = useState(() => new RequestTailBuffer())
  const [items, setItems] = useState<CollectionArrivalsPage["items"]>([])
  const query = useQuery({
    queryKey: ["request-observation-tail", id],
    queryFn: async ({signal}) => responseJson<CollectionArrivalsPage>(await fetch(`/api/collections/${encodeURIComponent(id)}/arrivals?limit=10`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache: "no-store",
    })),
    refetchInterval: playing ? 5000 : false,
    retry: false,
  })
  useEffect(() => {
    if (!query.data) return
    // Schedule presentation separately from React Query's server state.
    const timer = window.setTimeout(() => setItems(buffer.accept(query.data.items)), 0)
    return () => window.clearTimeout(timer)
  }, [buffer, query.data])
  useEffect(() => {
    if (!playing) return
    const timer = window.setInterval(() => {
      const next = buffer.advance(document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches)
      if (next) setItems(next)
    }, 500)
    return () => window.clearInterval(timer)
  }, [buffer, playing])
  return {items, query}
}
