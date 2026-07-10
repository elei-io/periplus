import { useActionRun } from "@/hooks/use-action-run"
import type { CrawlInput, CrawlOutput } from "@/types/crawl"

export function useCrawlRun() {
  return useActionRun<CrawlInput, CrawlOutput | null>("/crawl/", "Crawl", null)
}
