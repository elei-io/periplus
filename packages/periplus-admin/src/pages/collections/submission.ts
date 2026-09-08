import type { CollectionSpec } from "../../types/collections.ts"

export function collectionSubmission(form: FormData): CollectionSpec {
  const text = (name: string) => String(form.get(name) ?? "").trim()
  const integer = (name: string, min: number, max: number) => {
    const raw = text(name),
      value = Number(raw)
    if (!raw || !Number.isInteger(value) || value < min || value > max)
      throw new Error(
        `${name.replaceAll("_", " ")} must be an integer from ${min} to ${max}.`
      )
    return value
  }
  const lines = (name: string, max: number) => {
    const values = text(name)
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean)
    if (values.length > max)
      throw new Error(
        `${name.replaceAll("_", " ")} allows at most ${max} entries.`
      )
    return values
  }
  const seed_urls = lines("seed_urls", 1000)
  const seed_description = text("seed_description") || null
  const seed_sql = text("seed_sql") || null
  if (!seed_urls.length && !seed_description && !seed_sql)
    throw new Error("Provide starting URLs, a description, or seed SQL.")
  const seed_parameters: unknown = JSON.parse(text("seed_parameters") || "[]")
  if (!Array.isArray(seed_parameters) || seed_parameters.length > 100)
    throw new Error(
      "Seed parameters must be a JSON array with at most 100 entries."
    )
  if (seed_parameters.length && !seed_sql)
    throw new Error("Seed parameters require seed SQL.")
  const follow_sql = text("follow_sql")
  if (!follow_sql) throw new Error("Provide follow-link SQL.")
  const spec: CollectionSpec = {
    seed_urls,
    seed_description,
    seed_sql,
    seed_parameters,
    follow_sql,
    max_depth: integer("max_depth", 0, 100),
    page_limit: integer("page_limit", 1, 100000),
    retention_seconds: text("retention_seconds") ? integer("retention_seconds", 1, 315360000) : null,
    result_max_age_seconds: integer("result_max_age_seconds", 0, 3600),
    request_class: form.get("request_class") === "system" ? "system" : "admin",
    allowed_sections: lines("allowed_sections", 100),
    max_duration_seconds: text("max_duration_seconds") ? integer("max_duration_seconds", 1, 31536000) : null,
  }
  if (new TextEncoder().encode(JSON.stringify(spec)).length > 256 * 1024)
    throw new Error("Collection intent exceeds 256 KiB.")
  return spec
}
