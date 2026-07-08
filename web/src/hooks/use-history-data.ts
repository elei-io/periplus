import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  ArtifactDetailRecord,
  ArtifactFilters,
  ArtifactInvalidateRequest,
  ArtifactInvalidateResponse,
  ArtifactListResponse,
  CrawlDetailRecord,
  CrawlFilters,
  CrawlListResponse,
  PageParams,
  UrlDetailRecord,
  UrlFilters,
  UrlListResponse,
} from "@/types/history"

function appendParam(params: URLSearchParams, name: string, value: string) {
  const trimmed = value.trim()
  if (trimmed) {
    params.set(name, trimmed)
  }
}

function pageParams(page: PageParams) {
  return {
    limit: String(page.limit),
    offset: String(page.offset),
  }
}

function artifactParams(filters: ArtifactFilters, page: PageParams) {
  const params = new URLSearchParams(pageParams(page))
  appendParam(params, "url_pattern", filters.urlPattern)
  if (filters.kind !== "all") {
    params.set("kind", filters.kind)
  }
  if (filters.invalidated !== "all") {
    params.set("invalidated", String(filters.invalidated === "invalidated"))
  }
  if (filters.warnings !== "all") {
    params.set("warnings", String(filters.warnings === "warning"))
  }
  return params
}

function urlParams(filters: UrlFilters, page: PageParams) {
  const params = new URLSearchParams(pageParams(page))
  appendParam(params, "url_pattern", filters.urlPattern)
  appendParam(params, "domain", filters.domain)
  return params
}

function crawlParams(filters: CrawlFilters, page: PageParams) {
  const params = new URLSearchParams(pageParams(page))
  appendParam(params, "url_pattern", filters.urlPattern)
  appendParam(params, "domain", filters.domain)
  if (filters.success !== "all") {
    params.set("success", String(filters.success === "succeeded"))
  }
  appendParam(params, "status_code", filters.statusCode)
  if (filters.warnings !== "all") {
    params.set("warnings", String(filters.warnings === "warning"))
  }
  return params
}

export function useArtifacts(filters: ArtifactFilters, page: PageParams) {
  return useQuery({
    queryKey: ["artifacts", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/artifacts/?${artifactParams(filters, page).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ArtifactListResponse
    },
  })
}

export function useArtifact(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["artifact", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/artifacts/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ArtifactDetailRecord
    },
  })
}

export function useUrls(filters: UrlFilters, page: PageParams) {
  return useQuery({
    queryKey: ["urls", filters, page],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/urls/?${urlParams(filters, page).toString()}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as UrlListResponse
    },
  })
}

export function useUrl(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["url", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/urls/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as UrlDetailRecord
    },
  })
}

export function useCrawls(filters: CrawlFilters, page: PageParams) {
  return useQuery({
    queryKey: ["crawls", filters, page],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawls/?${crawlParams(filters, page).toString()}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlListResponse
    },
  })
}

export function useCrawl(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["crawl", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawls/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlDetailRecord
    },
  })
}

export function useInvalidateArtifacts() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: ArtifactInvalidateRequest) => {
      const response = await fetch(apiUrl("/artifacts/invalidate"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ArtifactInvalidateResponse
    },
    onSuccess: (result) => {
      toast.success(`Invalidated ${result.invalidated} artifacts.`)
      void queryClient.invalidateQueries({ queryKey: ["artifacts"] })
      void queryClient.invalidateQueries({ queryKey: ["urls"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}
