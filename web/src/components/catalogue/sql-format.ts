import { format } from "sql-formatter"

export function formatSql(value: string): string {
  if (!value.trim()) return value
  try {
    return format(value, {
      language: "duckdb",
      keywordCase: "upper",
      dataTypeCase: "upper",
      functionCase: "preserve",
      logicalOperatorNewline: "before",
      expressionWidth: 88,
      linesBetweenQueries: 1,
    })
  } catch {
    return value
  }
}
