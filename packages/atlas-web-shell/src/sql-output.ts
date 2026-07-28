import {
  formatSqlResult,
  formatTableResult,
  renderFormattedTable,
  sanitizeTerminalText,
  type ConsoleResult,
} from "atlas-console-core"

export function renderConsoleResult(
  output: ConsoleResult,
  columns: number
): string {
  if (output.kind === "clear") return "\u001b[2J\u001b[H"
  if (output.kind === "exit") return ""
  if (output.kind === "ai") return ""
  if (output.kind === "message") {
    return surround(
      sanitizeTerminalText(output.text, true).replaceAll("\n", "\r\n")
    )
  }
  const formatted =
    output.kind === "query"
      ? formatSqlResult(output.result, columns, output.durationMilliseconds)
      : formatTableResult(output, columns)
  if (!formatted.table.length) return surround("(no columns)")
  return surround(renderFormattedTable(formatted).join("\r\n"))
}

function surround(value: string): string {
  return `\r\n${value}\r\n\r\n`
}
