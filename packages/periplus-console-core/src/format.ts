import type { SqlResult, TableResult } from "./types.js"

const MAXIMUM_DISPLAY_ROWS = 100
const BLUE = "\u001b[1;34m"
const DIM = "\u001b[2m"
const YELLOW = "\u001b[33m"
const RESET = "\u001b[0m"

export interface FormattedSqlResult {
  table: string[]
  summary: string
}

export function formatSqlResult(
  result: SqlResult,
  maximumWidth: number,
  durationMilliseconds: number,
): FormattedSqlResult {
  const displayedRows = result.rows.slice(0, MAXIMUM_DISPLAY_ROWS)
  const omitted = result.rows.length - displayedRows.length
  const rowCount =
    omitted > 0
      ? `Showing ${displayedRows.length} of ${result.rows.length} returned rows`
      : `${result.rows.length} row${result.rows.length === 1 ? "" : "s"}`
  return {
    table: formatTable(result.columns, displayedRows, maximumWidth),
    summary:
      `${rowCount}${result.truncated ? " (server result truncated)" : ""}` +
      ` · ${formatDuration(durationMilliseconds)}`,
  }
}

export function formatTableResult(
  result: TableResult,
  maximumWidth: number,
): FormattedSqlResult {
  const displayedRows = result.rows.slice(0, MAXIMUM_DISPLAY_ROWS)
  const omitted = result.rows.length - displayedRows.length
  const displaySummary =
    omitted > 0
      ? `Showing ${displayedRows.length} of ${result.rows.length} rows`
      : undefined
  return {
    table: formatTable(result.columns, displayedRows, maximumWidth),
    summary: [displaySummary, result.summary].filter(Boolean).join(" · "),
  }
}

export function renderFormattedTable(
  formatted: FormattedSqlResult,
  color = true,
): string[] {
  if (!formatted.table.length) {
    return formatted.summary ? [dim(formatted.summary, color)] : []
  }
  const [header, separator, ...rows] = formatted.table
  return [
    color ? `${BLUE}${header}${RESET}` : header!,
    color ? `${DIM}${separator}${RESET}` : separator!,
    ...rows.map((row) => colorNullCells(row, color)),
    ...(formatted.summary ? [dim(formatted.summary, color)] : []),
  ]
}

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.max(1, Math.round(milliseconds))}ms`
  return `${(milliseconds / 1_000).toFixed(1)}s`
}

export function sanitizeTerminalText(
  value: string,
  preserveNewlines = false,
): string {
  const withoutAnsi = value.replace(
    /\u001b(?:\[[0-?]*[ -/]*[@-~]|\][^\u0007]*(?:\u0007|\u001b\\)?)/g,
    "",
  )
  const normalized = withoutAnsi
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .replaceAll("\t", " ")
  const lineSafe = preserveNewlines
    ? normalized
    : normalized.replaceAll("\n", " ")
  return [...lineSafe]
    .filter((character) => {
      const code = character.codePointAt(0) ?? 0
      if (preserveNewlines && character === "\n") return true
      return code >= 32 && !(code >= 127 && code <= 159)
    })
    .join("")
}

function formatTable(
  columns: string[],
  values: unknown[][],
  maximumWidth: number,
): string[] {
  if (!columns.length) return []
  const safeColumns = columns.map((column) => sanitizeTerminalText(column))
  const rows = values.map((row) =>
    safeColumns.map((_, column) => formatValue(row[column])),
  )
  const widths = safeColumns.map((column, index) =>
    Math.max(column.length, ...rows.map((row) => row[index]?.length ?? 0)),
  )
  shrinkWidths(widths, Math.max(maximumWidth, safeColumns.length * 4))
  const renderRow = (row: string[]) =>
    row
      .map((value, column) =>
        truncate(value, widths[column]!).padEnd(widths[column]!),
      )
      .join("  ")
      .trimEnd()
  return [
    renderRow(safeColumns),
    renderRow(widths.map((width) => "─".repeat(width))),
    ...rows.map(renderRow),
  ]
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL"
  if (value instanceof Uint8Array) {
    return `0x${[...value]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("")}`
  }
  if (value instanceof Date) return value.toISOString()
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "object") {
    try {
      return sanitizeTerminalText(JSON.stringify(value, jsonReplacer)).replaceAll(
        /\s+/g,
        " ",
      )
    } catch {
      return "[unserializable value]"
    }
  }
  return sanitizeTerminalText(String(value)).replaceAll(/\s+/g, " ")
}

function jsonReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") return value.toString()
  if (value instanceof Uint8Array) {
    return `0x${[...value]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("")}`
  }
  return value
}

function truncate(value: string, width: number): string {
  if ([...value].length <= width) return value
  return width <= 1 ? "…" : `${[...value].slice(0, width - 1).join("")}…`
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

function colorNullCells(row: string, color: boolean): string {
  if (!color) return row
  return row.replace(
    /(^| {2,})NULL(?= {2,}|$)/g,
    `$1${DIM}${YELLOW}NULL${RESET}`,
  )
}

function dim(value: string, color: boolean): string {
  return color ? `${DIM}${sanitizeTerminalText(value)}${RESET}` : value
}
