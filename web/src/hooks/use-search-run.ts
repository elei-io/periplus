import { useActionRun } from "@/hooks/use-action-run"
import type { SearchInput, SearchResult } from "@/types/search"

export function useSearchRun() {
  return useActionRun<SearchInput, SearchResult[]>("/search/", "Search", [])
}
