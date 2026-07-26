import type { SqlResult } from "./types.js"

export interface FormattedSqlResult {
  table: string[]
  summary: string
}

export function formatSqlResult(
  result: SqlResult,
  maximumWidth: number,
  durationMilliseconds: number,
): FormattedSqlResult {
  const rows = result.rows.map((row) =>
    result.columns.map((_, column) => formatValue(row[column])),
  )
  const widths = result.columns.map((column, index) =>
    Math.max(column.length, ...rows.map((row) => row[index]?.length ?? 0)),
  )
  shrinkWidths(widths, Math.max(maximumWidth, result.columns.length * 4))
  const renderRow = (row: string[]) =>
    row
      .map((value, column) =>
        truncate(value, widths[column]!).padEnd(widths[column]!),
      )
      .join("  ")
      .trimEnd()
  const rowCount = `${result.rows.length} row${result.rows.length === 1 ? "" : "s"}`
  return {
    table: [
      renderRow(result.columns),
      renderRow(widths.map((width) => "─".repeat(width))),
      ...rows.map(renderRow),
    ],
    summary:
      `${rowCount}${result.truncated ? " (truncated)" : ""}` +
      ` · ${formatDuration(durationMilliseconds)}`,
  }
}

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.max(1, Math.round(milliseconds))}ms`
  return `${(milliseconds / 1_000).toFixed(1)}s`
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL"
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "object") return JSON.stringify(value)
  return String(value).replaceAll(/\s+/g, " ")
}

function truncate(value: string, width: number): string {
  if (value.length <= width) return value
  return width <= 1 ? "…" : `${value.slice(0, width - 1)}…`
}

function shrinkWidths(widths: number[], maximumWidth: number): void {
  const separators = Math.max(0, widths.length - 1) * 2
  while (
    widths.reduce((total, width) => total + width, separators) > maximumWidth
  ) {
    const widest = Math.max(...widths)
    const index = widths.indexOf(widest)
    if (widest <= 3 || index < 0) break
    widths[index] = widest - 1
  }
}
