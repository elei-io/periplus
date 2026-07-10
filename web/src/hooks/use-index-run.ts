import { useActionRun } from "@/hooks/use-action-run"
import type { IndexInput } from "@/types/index"
import type { TaskResultSummary } from "@/types/tasks"

export function useIndexRun() {
  return useActionRun<IndexInput, TaskResultSummary | null>(
    "/index/",
    "Index",
    null
  )
}
