import { formatSqlResult, type QueryResult } from "atlas-console-core"

export function renderSqlResult(output: QueryResult, columns: number): string {
  if (output.result.columns.length === 0) return "(no columns)\r\n"
  const formatted = formatSqlResult(
    output.result,
    columns,
    output.durationMilliseconds,
  )
  return [
    ...formatted.table,
    `\u001b[2m${formatted.summary}\u001b[0m`,
    "",
  ].join("\r\n")
}
