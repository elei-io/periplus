import {
  formatSqlResult,
  formatTableResult,
  renderFormattedTable,
  sanitizeTerminalText,
  startProgress as startProgressTimer,
  type ConsoleResult,
} from "periplus-console-core"

export function renderConsoleResult(
  output: ConsoleResult,
  columns = process.stdout.columns ?? 100
): string {
  if (output.kind === "clear") return "\u001b[2J\u001b[H"
  if (output.kind === "exit") return ""
  if (output.kind === "message") {
    return surround(sanitizeTerminalText(output.text, true))
  }
  const formatted =
    output.kind === "query"
      ? formatSqlResult(output.result, columns, output.durationMilliseconds)
      : formatTableResult(output, columns)
  if (!formatted.table.length) return surround("(no columns)")
  return surround(
    renderFormattedTable(formatted, Boolean(process.stdout.isTTY)).join("\n")
  )
}

function surround(value: string): string {
  return `\n${value}\n\n`
}

export function startProgress(
  message: string,
  delayMilliseconds = 150
): { stop(): void } {
  if (!process.stdout.isTTY) return { stop() {} }
  return startProgressTimer(
    ({ symbol, elapsedSeconds }) =>
      process.stdout.write(
        `\r\u001b[2K\u001b[1;34m${symbol}\u001b[0m ${message} \u001b[2m${elapsedSeconds}s\u001b[0m`
      ),
    () => process.stdout.write("\r\u001b[2K"),
    delayMilliseconds
  )
}
