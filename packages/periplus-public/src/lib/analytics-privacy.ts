// URL inputs carry SQL, parameters and questions. Never send their values.
export function analyticsUrl(value: string): string {
  try {
    const url = new URL(value)
    if (url.protocol !== "https:" && url.protocol !== "http:") return "[redacted]"
    return `${url.origin}${url.pathname}`
  } catch { return "[redacted]" }
}

export function redactAnalyticsProperties(value: unknown, key = ""): unknown {
  if (/exception.*(message|value)|^(sql|parameters|prompt|question|rows|body|text|message|value|context_line|pre_context|post_context)$/i.test(key)) return "[redacted]"
  if (typeof value === "string") {
    if (/url|referrer|href|filename/i.test(key)) return analyticsUrl(value)
    return value
  }
  if (Array.isArray(value)) return value.map(item => redactAnalyticsProperties(item, key))
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, redactAnalyticsProperties(item, name)]))
  return value
}
