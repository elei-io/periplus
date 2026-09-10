const destinations = new Set(["/", "/discover", "/build", "/sql", "/coverage", "/docs", "/about"])

export function navigationAnalytics(href: string, current: string) {
  try {
    const from = new URL(current)
    const to = new URL(href, from)
    if (to.origin !== from.origin || !destinations.has(to.pathname)) return null
    const contextual = ["question", "draft", "sql", "description"].some(key => to.searchParams.has(key))
    return {
      event: contextual ? "workspace_handoff_opened" : "site_navigation_opened",
      properties: {
        from: destinations.has(from.pathname) ? from.pathname : "other",
        to: to.pathname,
        has_question: to.searchParams.has("question"),
        has_schema: to.searchParams.has("draft"),
        has_sql: to.searchParams.has("sql"),
        has_coverage_description: to.searchParams.has("description"),
      },
    }
  } catch { return null }
}
