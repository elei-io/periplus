import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CrawlPolicyCreateRequest,
  CrawlPolicyDetailRecord,
  CrawlPolicyFilters,
  CrawlPolicyListResponse,
  CrawlPolicyUpdateRequest,
  PageParams,
  DomainPolicyCreateRequest,
  DomainPolicyListResponse,
  DomainPolicyRecord,
  DomainPolicyUpdateRequest,
} from "@/types/resources"

function params(filters: CrawlPolicyFilters, page: PageParams) {
  const value = new URLSearchParams({ limit: String(page.limit), offset: String(page.offset) })
  if (filters.matchPattern.trim()) value.set("match_pattern", filters.matchPattern.trim())
  if (filters.enabled !== "all") value.set("enabled", String(filters.enabled === "enabled"))
  return value
}

export function useDomainPolicies(page: PageParams) {
  return useQuery({
    queryKey: ["domain-policies", page],
    queryFn: async () => {
      const query = new URLSearchParams({ limit: String(page.limit), offset: String(page.offset) })
      const response = await fetch(apiUrl(`/domain-policies/?${query}`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyListResponse
    },
  })
}

export function useCreateDomainPolicy() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: DomainPolicyCreateRequest) => {
      const response = await fetch(apiUrl("/domain-policies/"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyRecord
    },
    onSuccess: () => { toast.success("Created domain policy."); void client.invalidateQueries({ queryKey: ["domain-policies"] }) },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useUpdateDomainPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: DomainPolicyUpdateRequest) => {
      const response = await fetch(apiUrl(`/domain-policies/${id}`), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DomainPolicyRecord
    },
    onSuccess: () => { toast.success("Updated domain policy."); void client.invalidateQueries({ queryKey: ["domain-policies"] }) },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCrawlPolicies(filters: CrawlPolicyFilters, page: PageParams) {
  return useQuery({
    queryKey: ["crawl-policies", filters, page],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawl-policies/?${params(filters, page)}`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as CrawlPolicyListResponse
    },
  })
}

export function useCrawlPolicy(id: string | null) {
  return useQuery({
    enabled: Boolean(id), queryKey: ["crawl-policy", id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/crawl-policies/${id}`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as CrawlPolicyDetailRecord
    },
  })
}

export function useUpdateCrawlPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: CrawlPolicyUpdateRequest) => {
      const response = await fetch(apiUrl(`/crawl-policies/${id}`), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as CrawlPolicyDetailRecord
    },
    onSuccess: () => { toast.success("Updated crawl policy."); void client.invalidateQueries({ queryKey: ["crawl-policy", id] }); void client.invalidateQueries({ queryKey: ["crawl-policies"] }) },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCreateCrawlPolicy() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (request: CrawlPolicyCreateRequest) => {
      const response = await fetch(apiUrl("/crawl-policies/"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as CrawlPolicyDetailRecord
    },
    onSuccess: () => { toast.success("Created crawl policy."); void client.invalidateQueries({ queryKey: ["crawl-policies"] }) },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDeleteCrawlPolicy(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async () => { const response = await fetch(apiUrl(`/crawl-policies/${id}`), { method: "DELETE" }); if (!response.ok) throw await apiErrorFromResponse(response) },
    onSuccess: () => { toast.success("Deleted crawl policy."); void client.invalidateQueries({ queryKey: ["crawl-policies"] }) },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
