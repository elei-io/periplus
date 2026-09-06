export type CrawlProgress = {
  id: string
  status: string
  request_count: number
  pending_request_count: number
  failed_request_count: number
  created_at: string
  completed_at: string | null
}

export type CrawlReceipt = { receipt: string; status: string }

export function isCrawlProgress(value: unknown): value is CrawlProgress {
  if (!value || typeof value !== "object") return false
  const item = value as Record<string, unknown>
  return typeof item.id === "string" && typeof item.status === "string" &&
    [item.request_count, item.pending_request_count, item.failed_request_count].every(
      (count) => typeof count === "number" && Number.isInteger(count) && count >= 0
    ) && typeof item.created_at === "string" &&
    (item.completed_at === null || typeof item.completed_at === "string")
}
