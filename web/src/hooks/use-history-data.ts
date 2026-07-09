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
  ExtractSchemaDetailRecord,
  ExtractSchemaFilters,
  ExtractSchemaListResponse,
  ExtractSchemaUpdateRequest,
  HistoryMetricsResponse,
  PageParams,
  PaginationSchemaRecord,
  PaginationSchemaUpdateRequest,
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

function artifactParams(filters: ArtifactFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
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

function urlParams(filters: UrlFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
  appendParam(params, "url_pattern", filters.urlPattern)
  appendParam(params, "domain", filters.domain)
  return params
}

function crawlParams(filters: CrawlFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
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

function extractSchemaParams(filters: ExtractSchemaFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
  appendParam(params, "match_pattern", filters.matchPattern)
  appendParam(params, "prompt", filters.prompt)
  if (filters.schemaType !== "all") {
    params.set("schema_type", filters.schemaType)
  }
  if (filters.enabled !== "all") {
    params.set("enabled", String(filters.enabled === "enabled"))
  }
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

export function useArtifactMetrics(filters: ArtifactFilters) {
  return useQuery({
    queryKey: ["artifact-metrics", filters],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/artifacts/metrics?${artifactParams(filters).toString()}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as HistoryMetricsResponse
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

export function useUrlMetrics(filters: UrlFilters) {
  return useQuery({
    queryKey: ["url-metrics", filters],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/urls/metrics?${urlParams(filters).toString()}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as HistoryMetricsResponse
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

export function useCrawlMetrics(filters: CrawlFilters) {
  return useQuery({
    queryKey: ["crawl-metrics", filters],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawls/metrics?${crawlParams(filters).toString()}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as HistoryMetricsResponse
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

export function useExtractSchemas(filters: ExtractSchemaFilters, page: PageParams) {
  return useQuery({
    queryKey: ["extract-schemas", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/extract-schemas/?${extractSchemaParams(filters, page).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ExtractSchemaListResponse
    },
  })
}

export function useExtractSchemaMetrics(filters: ExtractSchemaFilters) {
  return useQuery({
    queryKey: ["extract-schema-metrics", filters],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/extract-schemas/metrics?${extractSchemaParams(filters).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as HistoryMetricsResponse
    },
  })
}

export function useExtractSchema(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["extract-schema", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/extract-schemas/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ExtractSchemaDetailRecord
    },
  })
}

export function useUpdateExtractSchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: ExtractSchemaUpdateRequest) => {
      const response = await fetch(apiUrl(`/extract-schemas/${id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as ExtractSchemaDetailRecord
    },
    onSuccess: () => {
      toast.success("Updated extract schema.")
      void queryClient.invalidateQueries({ queryKey: ["extract-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["extract-schemas"] })
      void queryClient.invalidateQueries({ queryKey: ["extract-schema-metrics"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}

export function usePaginationSchemas() {
  return useQuery({
    queryKey: ["pagination-schemas"],
    queryFn: async () => {
      const response = await fetch(apiUrl("/pagination-schemas/"))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as PaginationSchemaRecord[]
    },
  })
}

export function usePaginationSchema(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["pagination-schema", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/pagination-schemas/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as PaginationSchemaRecord
    },
  })
}

export function useUpdatePaginationSchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: PaginationSchemaUpdateRequest) => {
      const response = await fetch(apiUrl(`/pagination-schemas/${id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as PaginationSchemaRecord
    },
    onSuccess: () => {
      toast.success("Updated pagination schema.")
      void queryClient.invalidateQueries({ queryKey: ["pagination-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["pagination-schemas"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
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
      void queryClient.invalidateQueries({ queryKey: ["artifact-metrics"] })
      void queryClient.invalidateQueries({ queryKey: ["urls"] })
      void queryClient.invalidateQueries({ queryKey: ["url-metrics"] })
      void queryClient.invalidateQueries({ queryKey: ["url"] })
      void queryClient.invalidateQueries({ queryKey: ["crawls"] })
      void queryClient.invalidateQueries({ queryKey: ["crawl-metrics"] })
      void queryClient.invalidateQueries({ queryKey: ["crawl"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}
