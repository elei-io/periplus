// One bounded anonymous submission lane per application process. Shared ingress
// rate limits remain a deployment concern when running multiple replicas.
const WINDOW_MS = 60_000
const MAX_SUBMISSIONS = 10
let windowStart = 0
let submissions = 0

export function admitSubmission(now = Date.now()): boolean {
  if (now - windowStart >= WINDOW_MS) {
    windowStart = now
    submissions = 0
  }
  if (submissions >= MAX_SUBMISSIONS) return false
  submissions += 1
  return true
}

export function submissionUrl(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const item = value as Record<string, unknown>
  if (Object.keys(item).length !== 1 || typeof item.url !== "string" || item.url.length > 8192) return null
  try {
    const url = new URL(item.url)
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null
    url.hash = ""
    return url.href
  } catch {
    return null
  }
}

export function isSameOrigin(request: Request): boolean {
  try {
    const origin = new URL(request.headers.get("origin") ?? "")
    // Host preserves the browser-facing authority behind the container ingress.
    return ["http:", "https:"].includes(origin.protocol) && origin.host === request.headers.get("host")
  } catch {
    return false
  }
}
