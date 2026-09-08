import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  ContentPolicyCreateRequest,
  ContentPolicyDetailRecord,
  ContentPolicyFilters,
  ContentPolicyListResponse,
  ContentPolicyUpdateRequest,
  PageParams,
  DomainPolicyCreateRequest,
  DomainPolicyListResponse,
  DomainPolicyRecord,
  DomainPolicyUpdateRequest,
} from "@/types/resources"

function params(filters: ContentPolicyFilters, page: PageParams) {
  const value = new URLSearchParams({
    limit: String(page.limit),
    offset: String(page.offset),
  })
  if (filters.matchPattern.trim())
    value.set("match_pattern", filters.matchPattern.trim())
  if (filters.enabled !== "all")
    value.set("enabled", String(filters.enabled === "enabled"))
  return value
}

export function useDomainPolicies(page: PageParams) {
  return useQuery({
    queryKey: ["domain-policies", page],
    queryFn: async ({ signal }) => {
      const query = new URLSearchParams({
        limit: String(page.limit),
        offset: String(page.offset),
      })
      const response = await fetch(apiUrl(`/domain-policies/?${query}`), {
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyListResponse
    },
    refetchInterval: 5000,
    retry: false,
  })
}

export function useCreateDomainPolicy() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: DomainPolicyCreateRequest) => {
      const response = await fetch(apiUrl("/domain-policies/"), {
        method: "POST",
        signal: AbortSignal.timeout(15000),
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyRecord
    },
    onSuccess: () => {
      toast.success("Created domain policy.")
      void client.invalidateQueries({ queryKey: ["domain-policies"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateDomainPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: DomainPolicyUpdateRequest) => {
      const response = await fetch(apiUrl(`/domain-policies/${id}`), {
        method: "PATCH",
        signal: AbortSignal.timeout(15000),
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyRecord
    },
    onSuccess: () => {
      toast.success("Updated domain policy.")
      void client.invalidateQueries({ queryKey: ["domain-policies"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
      void client.invalidateQueries({ queryKey: ["domain-policies"] })
    },
  })
}

export function useContentPolicies(
  filters: ContentPolicyFilters,
  page: PageParams
) {
  return useQuery({
    queryKey: ["content-policies", filters, page],
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/content-policies/?${params(filters, page)}`)
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as ContentPolicyListResponse
    },
  })
}

export function useContentPolicy(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: ["content-policy", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/content-policies/${id}`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as ContentPolicyDetailRecord
    },
  })
}

export function useUpdateContentPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: ContentPolicyUpdateRequest) => {
      const response = await fetch(apiUrl(`/content-policies/${id}`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as ContentPolicyDetailRecord
    },
    onSuccess: () => {
      toast.success("Updated capture policy.")
      void client.invalidateQueries({ queryKey: ["content-policy", id] })
      void client.invalidateQueries({ queryKey: ["content-policies"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCreateContentPolicy() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: ContentPolicyCreateRequest) => {
      const response = await fetch(apiUrl("/content-policies/"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as ContentPolicyDetailRecord
    },
    onSuccess: () => {
      toast.success("Created capture policy.")
      void client.invalidateQueries({ queryKey: ["content-policies"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDeleteContentPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl(`/content-policies/${id}`), {
        method: "DELETE",
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      toast.success("Deleted capture policy.")
      void client.invalidateQueries({ queryKey: ["content-policies"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
