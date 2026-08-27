import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type {
  DocumentListParams,
  DocumentListResponse,
} from "@/types/documents"

function documentParams(filters: DocumentListParams) {
  const params = new URLSearchParams({
    limit: String(filters.limit),
    offset: String(filters.offset),
    sort: filters.sort,
    direction: filters.direction,
  })
  if (filters.contentType) params.set("content_type", filters.contentType)
  if (filters.url) params.set("url", filters.url)
  if (filters.observedFrom)
    params.set("observed_from", filters.observedFrom)
  if (filters.observedTo) params.set("observed_to", filters.observedTo)
  return params
}

export function useDocuments(filters: DocumentListParams) {
  return useQuery({
    queryKey: ["documents", filters],
    placeholderData: (previousData) => previousData,
    queryFn: async () => {
      const response = await fetch(
        apiUrl(`/documents?${documentParams(filters)}`)
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as DocumentListResponse
    },
  })
}

export function useDocumentMediaTypes() {
  return useQuery({
    queryKey: ["documents", "media-types"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const response = await fetch(apiUrl("/documents/media-types"))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as { items: string[] }
    },
  })
}

export function documentContentUrl(documentId: string) {
  return apiUrl(`/documents/${encodeURIComponent(documentId)}/content`)
}
