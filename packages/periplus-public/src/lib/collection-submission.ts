import type { CollectionSpec } from "../types/collections.ts"

export function publicCollectionSpec(input: { kind: "url" | "description"; input: string; depth: number; scope: "internal" | "external" | "both"; maxPages: number; sections: string; retentionSeconds?: number | null }): CollectionSpec {
  const value = input.input.trim()
  if (!value) throw new Error("Provide a starting URL or description.")
  if (!Number.isInteger(input.depth) || input.depth < 0 || input.depth > 100 || !Number.isInteger(input.maxPages) || input.maxPages < 1 || input.maxPages > 100000) throw new Error("Request values exceed supported bounds.")
  if (input.retentionSeconds != null && (!Number.isInteger(input.retentionSeconds) || input.retentionSeconds < 1 || input.retentionSeconds > 315360000)) throw new Error("Choose a valid retention period.")
  const sections = input.sections.split(/\r?\n/).map(v => v.trim()).filter(Boolean)
  if (sections.length > 10) throw new Error("Provide at most 10 allowed sections.")
  const follow_sql = "SELECT target_url AS url FROM nav.links" + (input.scope === "both" ? "" : input.scope === "internal" ? " WHERE relation_scope IN ('self', 'same_origin', 'same_host', 'same_site')" : " WHERE relation_scope = 'external'")
  return { seed_urls: input.kind === "url" ? [value] : [], seed_description: input.kind === "description" ? value : null, seed_sql: null, seed_parameters: [], follow_sql, max_depth: input.depth, page_limit: input.maxPages, result_max_age_seconds: 300, retention_seconds: input.retentionSeconds ?? null, request_class: "public", allowed_sections: sections, max_duration_seconds: null }
}
