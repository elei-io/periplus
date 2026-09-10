const destinations = new Set(["/", "/sql", "/coverage", "/docs", "/about"])

export function navigationAnalytics(href: string, current: string) {
  try {
    const from = new URL(current)
    const to = new URL(href, from)
    if (to.origin !== from.origin || !destinations.has(to.pathname)) return null
    const contextual = ["sql", "description"].some(key => to.searchParams.has(key))
    return {
      event: contextual ? "workspace_handoff_opened" : "site_navigation_opened",
      properties: {
        from: destinations.has(from.pathname) ? from.pathname : "other",
        to: to.pathname,
        has_sql: to.searchParams.has("sql"),
        has_coverage_description: to.searchParams.has("description"),
      },
    }
  } catch { return null }
}
