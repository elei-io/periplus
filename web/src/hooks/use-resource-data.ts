import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CrawlPolicyDetailRecord,
  CrawlPolicyFilters,
  CrawlPolicyListResponse,
  CrawlPolicyUpdateRequest,
  DataSchemaDetailRecord,
  DataSchemaFilters,
  DataSchemaListResponse,
  DataSchemaUpdateRequest,
  PageParams,
  QuerySchemaDetailRecord,
  QuerySchemaFilters,
  QuerySchemaListResponse,
  QuerySchemaUpdateRequest,
} from "@/types/resources"

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

export function useCrawlPolicies(
  filters: CrawlPolicyFilters,
  page: PageParams
) {
  return useQuery({
    queryKey: ["crawl-policies", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(
          `/crawl-policies/?${crawlPolicyParams(filters, page).toString()}`
        )
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
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })
}
