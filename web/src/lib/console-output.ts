import type { AtomicCommandResult, ConsoleStatus } from "@atlas/console-core"

const BLUE = "\u001b[1;34m"
const VIOLET = "\u001b[1;35m"
const DIM = "\u001b[2m"
const GREEN = "\u001b[32m"
const YELLOW = "\u001b[33m"
const RED = "\u001b[31m"
const RESET = "\u001b[0m"
const MAXIMUM_DISPLAY_ROWS = 100

export function renderConsoleResult(
  result: AtomicCommandResult,
  columns: number
): string {
  if (result.kind === "progress") {
    const symbol =
      result.state === "completed"
        ? `${GREEN}✓${RESET}`
        : result.state === "failed"
          ? `${RED}✗${RESET}`
          : activitySymbol(result.activity)
    const duration =
      result.durationMilliseconds === undefined
        ? ""
        : ` ${DIM}· ${formatDuration(result.durationMilliseconds)}${RESET}`
    return `${DIM}│${RESET} ${symbol} ${safeText(result.label)}${duration}\r\n`
  }
  if (result.kind === "assistant") {
    return renderAssistant(result, columns)
  }
  if (result.kind === "message") return `${result.text}\r\n`
  if (result.kind === "navigate") return `${BLUE}${result.label}${RESET}\r\n`
  if (result.kind === "clear") return "\u001b[2J\u001b[H"

  const displayedRows = result.rows.slice(0, MAXIMUM_DISPLAY_ROWS)
  const table = formatTable(
    result.columns,
    displayedRows,
    Math.max(20, columns - 1)
  ).join("\r\n")
  const omitted = result.rows.length - displayedRows.length
  const truncation =
    omitted > 0
      ? `\r\n${DIM}Showing ${displayedRows.length} of ${result.rows.length} returned rows · use the Atlas SDK for full-result analysis.${RESET}`
      : ""
  const summary = result.summary
    ? `\r\n${DIM}${safeText(result.summary)}${RESET}`
    : ""
  return `${table}${truncation}${summary}\r\n`
}

export function renderConsoleProgress(
  frame: string,
  label: string
): string {
  return `\r\u001b[2K${DIM}│${RESET} ${VIOLET}${frame}${RESET} ${safeText(label)}`
}

export function renderConsoleAssistantHeading(): string {
  return `\r\n${VIOLET}◆ Atlas${RESET}\r\n`
}

export function renderConsoleError(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason)
  return `${RED}Error: ${safeText(message)}${RESET}\r\n`
}

export function renderConsoleWelcome(status: ConsoleStatus): string {
  const lake = status.lakeSlug ?? "—"
  const schema = status.schemaVersion ?? "—"
  const compiler = status.compilerVersion ?? "—"
  return [
    "",
    "     ___  ________  ___   _____",
    "    / _ |/_  __/ / / _ | / ___/",
    "   / __ | / / / /_/ __ |(__  )",
    "  /_/ |_|/_/ /___/_/ |_/____/",
    "",
    "The web is messy. Let's make it queryable.",
    `Lake ${lake} · Atlas schema ${schema} · compiler ${compiler} · .help knows the terrain.`,
    "",
    "",
  ].join("\r\n")
}

export function formatTable(
  columns: string[],
  rows: unknown[][],
  maximumWidth: number
): string[] {
  if (columns.length === 0) return []
  const textRows = rows.map((row) =>
    columns.map((_column, index) => formatValue(row[index]))
  )
  const widths = columns.map((column, index) =>
    Math.max(column.length, ...textRows.map((row) => row[index]?.length ?? 0))
  )
  shrinkWidths(widths, Math.max(maximumWidth, columns.length * 4))
  const render = (row: string[]) =>
    row
      .map((value, index) => truncate(value, widths[index] ?? 1))
      .map((value, index) => value.padEnd(widths[index] ?? 1))
      .join("  ")
      .trimEnd()
  return [
    `${BLUE}${render(columns)}${RESET}`,
    `${DIM}${render(widths.map((width) => "─".repeat(width)))}${RESET}`,
    ...textRows.map(render),
  ]
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

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL"
  if (value instanceof Uint8Array) {
    return `0x${[...value]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("")}`
  }
  if (value instanceof Date) return value.toISOString()
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "object") return JSON.stringify(value, jsonReplacer)
  return safeText(String(value))
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

function safeText(value: string): string {
  return [...value.replaceAll("\r\n", " ").replaceAll("\r", " ").replaceAll("\n", " ")]
    .filter((character) => {
      const code = character.codePointAt(0) ?? 0
      return code >= 32 && !(code >= 127 && code <= 159)
    })
    .join("")
}

function activitySymbol(activity: "thinking" | "catalogue" | "sql"): string {
  if (activity === "catalogue") return `${YELLOW}◇${RESET}`
  if (activity === "sql") return `${BLUE}▸${RESET}`
  return `${BLUE}◌${RESET}`
}

function formatDuration(milliseconds: number): string {
  return milliseconds < 1_000
    ? `${milliseconds}ms`
    : `${(milliseconds / 1_000).toFixed(1)}s`
}

function renderAssistant(
  result: Extract<AtomicCommandResult, { kind: "assistant" }>,
  columns: number
): string {
  const width = Math.max(24, Math.min(100, columns - 4))
  const lines = wrapText(result.text, width)
  const error = result.state === "failed"
  const answer = lines
    .map((line, index) => {
      const prefix = index === 0 ? "└ " : "  "
      const text = error ? `${RED}${line}${RESET}` : line
      return `${DIM}${prefix}${RESET}${text}`
    })
    .join("\r\n")
  if (!result.sql) {
    const suggestions = (result.suggestions ?? [])
      .map(
        (suggestion) =>
          `${DIM}│${RESET}  ${BLUE}${suggestion.index}.${RESET} ${suggestion.title}` +
          ` ${DIM}— ${suggestion.description}${RESET}`,
      )
      .join("\r\n")
    const actions = result.suggestions?.length
      ? `${DIM}└ .ai show <number> · .ai run <number>${RESET}\r\n\r\n`
      : ""
    return (
      `${DIM}│${RESET}\r\n${answer}\r\n` +
      (suggestions ? `${DIM}│${RESET}\r\n${suggestions}\r\n${actions}` : "\r\n")
    )
  }

  const sql = result.sql
    .replaceAll("\r\n", "\n")
    .split("\n")
    .map((line) => `${DIM}│   ${safeText(line)}${RESET}`)
    .join("\r\n")
  return (
    `${DIM}│${RESET}\r\n` +
    `${lines.map((line) => `${DIM}│${RESET} ${line}`).join("\r\n")}\r\n` +
    `${DIM}│${RESET}\r\n${sql}\r\n` +
    `${DIM}└ Run with ${result.sqlRunCommand ?? ".ai run"}${RESET}\r\n\r\n`
  )
}

function wrapText(value: string, width: number): string[] {
  const output: string[] = []
  for (const paragraph of value.replaceAll("\r\n", "\n").split("\n")) {
    const words = safeText(paragraph).split(/\s+/).filter(Boolean)
    if (words.length === 0) {
      output.push("")
      continue
    }
    let line = ""
    for (const word of words) {
      if (line && line.length + word.length + 1 > width) {
        output.push(line)
        line = word
      } else {
        line = line ? `${line} ${word}` : word
      }
    }
    if (line) output.push(line)
  }
  return output.length ? output : [""]
}

function truncate(value: string, width: number): string {
  if ([...value].length <= width) return value
  if (width <= 1) return "…"
  return `${[...value].slice(0, width - 1).join("")}…`
}
