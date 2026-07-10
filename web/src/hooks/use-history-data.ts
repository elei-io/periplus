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
  CrawlPolicyDetailRecord,
  CrawlPolicyFilters,
  CrawlPolicyListResponse,
  CrawlPolicyUpdateRequest,
  DataSchemaDetailRecord,
  DataSchemaFilters,
  DataSchemaListResponse,
  DataSchemaUpdateRequest,
  HistoryMetricsResponse,
  PageParams,
  QuerySchemaDetailRecord,
  QuerySchemaFilters,
  QuerySchemaListResponse,
  QuerySchemaUpdateRequest,
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

function querySchemaParams(filters: QuerySchemaFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
  appendParam(params, "match_pattern", filters.matchPattern)
  appendParam(params, "domain", filters.domain)
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

function crawlPolicyParams(filters: CrawlPolicyFilters, page?: PageParams) {
  const params = new URLSearchParams(page ? pageParams(page) : undefined)
  appendParam(params, "match_pattern", filters.matchPattern)
  appendParam(params, "template", filters.template)
  if (filters.enabled !== "all") {
    params.set("enabled", String(filters.enabled === "enabled"))
  }
  if (filters.mode !== "all") {
    params.set("mode", filters.mode)
  }
  return params
}

function dataSchemaParams(filters: DataSchemaFilters, page?: PageParams) {
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

export function useQuerySchemas(filters: QuerySchemaFilters, page: PageParams) {
  return useQuery({
    queryKey: ["query-schemas", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/query-schemas/?${querySchemaParams(filters, page).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as QuerySchemaListResponse
    },
  })
}

export function useQuerySchema(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["query-schema", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/query-schemas/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as QuerySchemaDetailRecord
    },
  })
}

export function useUpdateQuerySchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: QuerySchemaUpdateRequest) => {
      const response = await fetch(apiUrl(`/query-schemas/${id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as QuerySchemaDetailRecord
    },
    onSuccess: () => {
      toast.success("Updated query schema.")
      void queryClient.invalidateQueries({ queryKey: ["query-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["query-schemas"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}

export function useDeleteQuerySchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl(`/query-schemas/${id}`), {
        method: "DELETE",
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
    },
    onSuccess: () => {
      toast.success("Deleted query schema.")
      void queryClient.invalidateQueries({ queryKey: ["query-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["query-schemas"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
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

export function useCrawlPolicies(filters: CrawlPolicyFilters, page: PageParams) {
  return useQuery({
    queryKey: ["crawl-policies", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/crawl-policies/?${crawlPolicyParams(filters, page).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlPolicyListResponse
    },
  })
}

export function useCrawlPolicy(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["crawl-policy", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawl-policies/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlPolicyDetailRecord
    },
  })
}

export function useUpdateCrawlPolicy(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: CrawlPolicyUpdateRequest) => {
      const response = await fetch(apiUrl(`/crawl-policies/${id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlPolicyDetailRecord
    },
    onSuccess: () => {
      toast.success("Updated crawl policy.")
      void queryClient.invalidateQueries({ queryKey: ["crawl-policy", id] })
      void queryClient.invalidateQueries({ queryKey: ["crawl-policies"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}

export function useDeleteCrawlPolicy(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl(`/crawl-policies/${id}`), {
        method: "DELETE",
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
    },
    onSuccess: () => {
      toast.success("Deleted crawl policy.")
      void queryClient.invalidateQueries({ queryKey: ["crawl-policy", id] })
      void queryClient.invalidateQueries({ queryKey: ["crawl-policies"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}

export function useDataSchemas(filters: DataSchemaFilters, page: PageParams) {
  return useQuery({
    queryKey: ["data-schemas", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/data-schemas/?${dataSchemaParams(filters, page).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as DataSchemaListResponse
    },
  })
}

export function useDataSchemaMetrics(filters: DataSchemaFilters) {
  return useQuery({
    queryKey: ["data-schema-metrics", filters],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/data-schemas/metrics?${dataSchemaParams(filters).toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as HistoryMetricsResponse
    },
  })
}

export function useDataSchema(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["data-schema", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/data-schemas/${id}`))
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as DataSchemaDetailRecord
    },
  })
}

export function useUpdateDataSchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: DataSchemaUpdateRequest) => {
      const response = await fetch(apiUrl(`/data-schemas/${id}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as DataSchemaDetailRecord
    },
    onSuccess: () => {
      toast.success("Updated data schema.")
      void queryClient.invalidateQueries({ queryKey: ["data-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["data-schemas"] })
      void queryClient.invalidateQueries({ queryKey: ["data-schema-metrics"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}

export function useDeleteDataSchema(id: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl(`/data-schemas/${id}`), {
        method: "DELETE",
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
    },
    onSuccess: () => {
      toast.success("Deleted data schema.")
      void queryClient.invalidateQueries({ queryKey: ["data-schema", id] })
      void queryClient.invalidateQueries({ queryKey: ["data-schemas"] })
      void queryClient.invalidateQueries({ queryKey: ["data-schema-metrics"] })
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
