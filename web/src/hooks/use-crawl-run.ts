import { useActionRun } from "@/hooks/use-action-run"
import type { CrawlInput } from "@/types/crawl"
import type { TaskResultSummary } from "@/types/tasks"

export function useCrawlRun() {
  return useActionRun<CrawlInput, TaskResultSummary | null>(
    "/crawl/",
    "Crawl",
    null
  )
}
