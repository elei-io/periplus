import { useActionRun } from "@/hooks/use-action-run"
import type { SearchInput } from "@/types/search"
import type { TaskResultSummary } from "@/types/tasks"

export function useSearchRun() {
  return useActionRun<SearchInput, TaskResultSummary | null>(
    "/search/",
    "Search",
    null
  )
}
