"use client"

import { keepPreviousData, useQuery } from "@tanstack/react-query"

import {
  isDomainListResponse,
  type DomainListResponse,
} from "@/types/public-catalogue"

export interface UseDomainsInput {
  search: string
  cursor: string | null
  limit?: number
}

export function useDomains({
  search,
  cursor,
  limit = 25,
}: UseDomainsInput) {
  return useQuery({
    queryKey: ["domains", { search, cursor, limit }],
    queryFn: ({ signal }) => fetchDomains({ search, cursor, limit }, signal),
    placeholderData: keepPreviousData,
  })
}

async function fetchDomains(
  { search, cursor, limit }: Required<UseDomainsInput>,
  signal: AbortSignal
): Promise<DomainListResponse> {
  const parameters = new URLSearchParams({ limit: String(limit) })
  if (search) parameters.set("search", search)
  if (cursor) parameters.set("cursor", cursor)

  const response = await fetch(`/api/domains?${parameters}`, { signal })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : undefined
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Domain request failed with status ${response.status}`
    )
  }
  if (!isDomainListResponse(body)) {
    throw new Error("The domain API returned an incompatible response.")
  }
  return body
}
