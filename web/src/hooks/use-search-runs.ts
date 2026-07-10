import {
  useActionRuns,
  useCancelActionRun,
  useSubmitAction,
} from "@/hooks/use-action-runs"
import type { SearchInput } from "@/types/search"

export function useSearchRuns() {
  return useActionRuns("search")
}

export function useSubmitSearch() {
  return useSubmitAction<SearchInput>("search", "/search/")
}

export function useCancelSearchRun() {
  return useCancelActionRun("search")
}
