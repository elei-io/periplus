import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CrawlPolicyDetailRecord,
  CrawlPolicyFilters,
  CrawlPolicyListResponse,
  CrawlPolicyUpdateRequest,
  PageParams,
  PolicyTrialApplyRequest,
  PolicyTrialReport,
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

export function usePolicyTrials(page: PageParams) {
  return useQuery({
    queryKey: ["policy-trials", page],
    queryFn: async () => {
      const params = new URLSearchParams(pageParams(page))
      const response = await fetch(
        apiUrl(`/crawl-policies/trials?${params.toString()}`)
      )
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as PolicyTrialReport
    },
  })
}

export function useApplyPolicyTrial() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: PolicyTrialApplyRequest) => {
      const response = await fetch(apiUrl("/crawl-policies/trials/apply"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      })
      if (!response.ok) {
        throw await apiErrorFromResponse(response)
      }
      return (await response.json()) as CrawlPolicyDetailRecord
    },
    onSuccess: () => {
      toast.success("Applied sampled crawl policy.")
      void queryClient.invalidateQueries({ queryKey: ["policy-trials"] })
      void queryClient.invalidateQueries({ queryKey: ["crawl-policies"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
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
