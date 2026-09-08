import { displayValue } from "./query-values.ts"

export function serializeQueryResults(result: { columns: string[]; types: string[]; rows: unknown[][] }, format: "csv" | "json") {
  if (format === "json") return JSON.stringify({ columns: result.columns, types: result.types, rows: result.rows }, (_, value) => typeof value === "bigint" ? String(value) : value, 2)
  return [result.columns, ...result.rows].map(row => row.map(value => `"${displayValue(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
}
