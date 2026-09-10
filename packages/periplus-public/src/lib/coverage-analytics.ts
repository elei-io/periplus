"use client"

import { captureAnalytics } from "./analytics"
import type { Collection } from "@/types/collections"

const storageKey = "periplus.analytics.requests"
type Receipt = { submitted: number; ready: boolean; settled: boolean }
function receipts(): Record<string, Receipt> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "{}")
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter(([, item]) => item && typeof item.submitted === "number" && Number.isFinite(item.submitted) && item.submitted > Date.now() - 30 * 86400000 && typeof item.ready === "boolean" && typeof item.settled === "boolean").slice(-50))
  } catch { return {} }
}
export function rememberCoverageSubmission(id: string) {
  try {
    const items = receipts()
    if (items[id]) return
    items[id] = { submitted: Date.now(), ready: false, settled: false }
    localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(Object.entries(items).slice(-50))))
  } catch { /* Storage is optional. */ }
}
export function observeCoverage(item: Collection) {
  try {
    const items = receipts(), receipt = items[item.id]
    if (!receipt) return // Never attribute somebody else's request to this visitor.
    const ready = item.query_ready === true && !receipt.ready
    const settled = !!item.completed_at && !receipt.settled
    if (!ready && !settled) return
    items[item.id] = { ...receipt, ready: receipt.ready || ready, settled: receipt.settled || settled }
    localStorage.setItem(storageKey, JSON.stringify(items))
    const properties = { request_id: item.id, observation: "submitting_browser", observed_elapsed_ms: Math.max(0, Date.now() - receipt.submitted), supplied_pages: item.supplied_pages }
    if (ready) captureAnalytics("coverage_request_ready_observed", properties)
    if (settled) captureAnalytics("coverage_request_settled_observed", { ...properties, outcome: item.outcome })
  } catch { /* Storage/analytics must not affect request progress. */ }
}
