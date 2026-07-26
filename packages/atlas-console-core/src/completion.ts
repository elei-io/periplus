import type { Completion, SqlMetadata, SqlRelation } from "./types.js"

const KEYWORDS = [
  "SELECT",
  "FROM",
  "WHERE",
  "JOIN",
  "LEFT JOIN",
  "INNER JOIN",
  "ON",
  "GROUP BY",
  "ORDER BY",
  "LIMIT",
  "WITH",
  "AS",
  "AND",
  "OR",
  "COUNT",
  "DISTINCT",
] as const

export class SqlCompleter {
  private metadata?: SqlMetadata

  constructor(private readonly loadMetadata: () => Promise<SqlMetadata>) {}

  async complete(input: string, cursor = input.length): Promise<Completion[]> {
    this.metadata ??= await this.loadMetadata()
    const { prefix, start } = currentToken(input, cursor)
    const values = new Map<string, Completion>()
    const add = (value: string, kind: Completion["kind"]) => {
      if (!value.toLowerCase().startsWith(prefix.toLowerCase())) return
      values.set(`${kind}:${value.toLowerCase()}`, {
        value,
        replaceStart: start,
        replaceEnd: cursor,
        kind,
      })
    }

    for (const keyword of KEYWORDS) add(keyword, "keyword")
    for (const schema of ["ingest", "material"]) add(schema, "schema")
    for (const relation of this.metadata.relations) {
      add(`${relation.schema_name}.${relation.name}`, "relation")
      add(relation.name, "relation")
    }
    for (const column of columnsInScope(input, this.metadata.relations)) {
      add(column, "column")
    }
    return [...values.values()].sort((left, right) =>
      left.value.localeCompare(right.value),
    )
  }

  clear(): void {
    this.metadata = undefined
  }
}

function currentToken(input: string, cursor: number) {
  const before = input.slice(0, cursor)
  const match = before.match(/[A-Za-z_][A-Za-z0-9_.]*$/)
  return {
    prefix: match?.[0] ?? "",
    start: match ? cursor - match[0].length : cursor,
  }
}

function columnsInScope(input: string, relations: SqlRelation[]): string[] {
  const lower = input.toLowerCase()
  const found = relations.filter((relation) => {
    const qualified = `${relation.schema_name}.${relation.name}`.toLowerCase()
    return (
      lower.includes(qualified) ||
      new RegExp(`\\b${escapeRegExp(relation.name.toLowerCase())}\\b`).test(lower)
    )
  })
  return [...new Set(found.flatMap((relation) => relation.columns.map((item) => item.name)))]
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
