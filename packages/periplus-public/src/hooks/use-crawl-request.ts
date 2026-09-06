"use client"

import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { extractApiError, responseJson } from "@/lib/api"
import type { CrawlProgress, CrawlReceipt } from "@/types/crawls"

export function useCrawlRequest(receipt: string | null) {
  const submission = useMutation({
    mutationFn: async (url: string) => responseJson<CrawlReceipt>(await fetch("/api/crawls", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url }),
    })),
    onError: (error) => toast.error(extractApiError(error)),
  })
  const progress = useQuery({
    queryKey: ["crawl-request", receipt],
    enabled: Boolean(receipt),
    queryFn: async ({ signal }) => responseJson<CrawlProgress>(await fetch(`/api/crawls/${encodeURIComponent(receipt!)}`, { signal })),
    refetchInterval: (query) => {
      if (query.state.error) return false
      return ["queued", "running", "paused"].includes(query.state.data?.status ?? "queued") ? 3000 : false
    },
    retry: false,
  })
  return { submission, progress }
}
