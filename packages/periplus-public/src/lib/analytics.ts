"use client"

import posthog from "posthog-js"
import { createRequestId } from "./request-id"
import { ApiError } from "./api"
import type { AnalyticsOperation, AnalyticsProperties } from "@/types/analytics"

export function captureAnalytics(event: string, properties: AnalyticsProperties = {}) {
  // Analytics must never change the outcome of a user operation.
  try { posthog.capture(event, properties) } catch { /* Best effort. */ }
}
export function startAnalyticsOperation(event: string, properties: AnalyticsProperties = {}): AnalyticsOperation {
  const operation = { id: createRequestId(), started: performance.now(), finished: false }
  captureAnalytics(event, { ...properties, operation_id: operation.id })
  return operation
}
export function finishAnalyticsOperation(operation: AnalyticsOperation, event: string, properties: AnalyticsProperties = {}) {
  if (operation.finished) return
  operation.finished = true
  captureAnalytics(event, { ...properties, operation_id: operation.id, duration_ms: Math.round(performance.now() - operation.started) })
}
export function analyticsErrorCategory(error: unknown): string {
  if (error instanceof Error && error.name === "AbortError") return "cancelled"
  if (error instanceof Error && error.name === "TimeoutError") return "timeout"
  if (error instanceof ApiError) {
    if (error.status === 429) return "rate_limit"
    if (error.status === 401 || error.status === 403) return "access"
    if (error.status >= 500) return "service"
    return "invalid_request"
  }
  return "network_or_client"
}
