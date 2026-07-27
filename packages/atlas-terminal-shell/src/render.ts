import {
  formatSqlResult,
  startProgress as startProgressTimer,
  type ConsoleResult,
} from "atlas-console-core"

export function renderConsoleResult(
  output: ConsoleResult,
  columns = process.stdout.columns ?? 100,
): string {
  if (output.kind === "clear") return "\u001b[2J\u001b[H"
  if (output.kind === "exit") return ""
  if (output.kind === "message") return `${output.text}\n`
  if (output.result.columns.length === 0) return "(no columns)\n"
  const formatted = formatSqlResult(
    output.result,
    columns,
    output.durationMilliseconds,
  )
  return [
    ...formatted.table,
    `\u001b[2m${formatted.summary}\u001b[0m`,
    "",
  ].join("\n")
}

export function startProgress(
  message: string,
  delayMilliseconds = 150,
): { stop(): void } {
  if (!process.stdout.isTTY) return { stop() {} }
  return startProgressTimer(
    ({ symbol, elapsedSeconds }) =>
      process.stdout.write(
        `\r\u001b[2K\u001b[1;34m${symbol}\u001b[0m ${message} \u001b[2m${elapsedSeconds}s\u001b[0m`,
      ),
    () => process.stdout.write("\r\u001b[2K"),
    delayMilliseconds,
  )
}
