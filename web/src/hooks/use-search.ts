import { useQuery } from "@tanstack/react-query"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type {
  SearchCompilation,
  SearchResultType,
  SearchTypeRegistry,
} from "@/types/search"

async function fetchSearchTypes(): Promise<SearchTypeRegistry> {
  const response = await fetch(apiUrl("/search/types"))
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<SearchTypeRegistry>
}

export async function compileSearch(
  query: string,
  resultType: SearchResultType,
  limit = 50
): Promise<SearchCompilation> {
  const response = await fetch(apiUrl("/search/compile"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      result_type: resultType,
      limit,
    }),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<SearchCompilation>
}

export function useSearchTypes() {
  return useQuery({
    queryKey: ["search-types"],
    queryFn: fetchSearchTypes,
    staleTime: 5 * 60_000,
  })
}
