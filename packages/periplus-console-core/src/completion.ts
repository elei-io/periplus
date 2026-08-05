import type {
  Completion,
  SqlColumn,
  SqlMacro,
  SqlMetadata,
  SqlRelation,
} from "./types.js"

const KEYWORDS = [
  "SELECT",
  "FROM",
  "WHERE",
  "JOIN",
  "LEFT JOIN",
  "RIGHT JOIN",
  "INNER JOIN",
  "ON",
  "GROUP BY",
  "ORDER BY",
  "HAVING",
  "LIMIT",
  "OFFSET",
  "WITH",
  "UNION ALL",
  "AS",
  "AND",
  "OR",
  "COUNT",
  "DISTINCT",
  "DESCRIBE",
  "EXPLAIN",
  "EXPLAIN ANALYZE",
  "SUMMARIZE",
  "SHOW TABLES FROM web",
  "SHOW TABLES FROM dom",
] as const

const CLAUSE_WORDS = new Set([
  "and",
  "cross",
  "full",
  "group",
  "having",
  "inner",
  "join",
  "left",
  "limit",
  "offset",
  "on",
  "order",
  "right",
  "union",
  "where",
])

interface ScopedRelation {
  alias: string
  name: string
  columns: SqlColumn[]
}

export class SqlCompleter {
  private cached?: SqlMetadata
  private fetchedAt = 0
  private pending?: Promise<SqlMetadata>

  constructor(
    private readonly loadMetadata: () => Promise<SqlMetadata>,
    private readonly metadataTtlMilliseconds = 30_000,
  ) {}

  async complete(input: string, cursor = input.length): Promise<Completion[]> {
    if (input.slice(0, cursor).trimEnd().endsWith(";")) return []
    const metadata = await this.metadata()
    const target = currentToken(input, cursor)
    const beforeTarget = input.slice(0, target.start)
    const expectsRelation = relationExpected(beforeTarget)
    const scoped = relationsInScope(input, metadata)
    const publicSchemas = [
      ...new Set(
        [...metadata.relations, ...metadata.macros].map(
          (item) => item.schema_name,
        ),
      ),
    ].sort()
    const selectedSchema = publicSchemas.find((schema) =>
      target.prefix.toLowerCase().startsWith(`${schema}.`),
    )
    const values = new Map<string, Completion>()
    const add = (
      value: string,
      kind: Completion["kind"],
      description?: string,
      priority = 0,
    ) => {
      if (!value.toLowerCase().startsWith(target.prefix.toLowerCase())) return
      const key = `${target.start}:${value.toLowerCase()}`
      const existing = values.get(key)
      if (existing && (existing.priority ?? 0) >= priority) return
      values.set(key, {
        value,
        replaceStart: target.start,
        replaceEnd: cursor,
        kind,
        description,
        priority,
      })
    }

    if (selectedSchema) {
      if (expectsRelation) {
        for (const relation of metadata.relations.filter(
          (item) => item.schema_name === selectedSchema,
        )) {
          add(
            qualified(relation),
            "relation",
            relationDescription(relation),
            90,
          )
        }
      }
      for (const macro of metadata.macros.filter(
        (item) =>
          item.schema_name === selectedSchema &&
          (expectsRelation
            ? item.kind === "table_macro"
            : item.kind === "scalar_macro"),
      )) {
        add(
          `${qualified(macro)}(`,
          "function",
          macroDescription(macro),
          90,
        )
      }
    } else if (expectsRelation) {
      for (const schema of publicSchemas) {
        add(`${schema}.`, "schema", "public catalogue", 80)
      }
      for (const relation of metadata.relations) {
        add(
          qualified(relation),
          "relation",
          relationDescription(relation),
          90,
        )
      }
      for (const macro of metadata.macros.filter(
        (item) => item.kind === "table_macro",
      )) {
        add(
          `${qualified(macro)}(`,
          "function",
          macroDescription(macro),
          90,
        )
      }
    } else {
      addColumns(add, target.prefix, scoped)
      for (const macro of metadata.macros.filter(
        (item) => item.kind === "scalar_macro",
      )) {
        add(
          `${qualified(macro)}(`,
          "function",
          macroDescription(macro),
          60,
        )
      }
      for (const keyword of KEYWORDS) add(keyword, "keyword", "SQL keyword", 20)
    }

    return [...values.values()].sort(
      (left, right) =>
        (right.priority ?? 0) - (left.priority ?? 0) ||
        left.value.localeCompare(right.value),
    )
  }

  async metadata(force = false): Promise<SqlMetadata> {
    if (
      force ||
      (this.cached &&
        Date.now() - this.fetchedAt >= this.metadataTtlMilliseconds)
    ) {
      this.clear()
    }
    if (this.cached) return this.cached
    this.pending ??= this.loadMetadata()
      .then((metadata) => {
        this.cached = metadata
        this.fetchedAt = Date.now()
        this.pending = undefined
        return metadata
      })
      .catch((error: unknown) => {
        this.pending = undefined
        throw error
      })
    return this.pending
  }

  clear(): void {
    this.cached = undefined
    this.fetchedAt = 0
    this.pending = undefined
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

function relationExpected(input: string): boolean {
  return /(?:\bFROM|\bJOIN|,)\s+[A-Za-z0-9_.]*$/i.test(input)
}

function relationsInScope(input: string, metadata: SqlMetadata): ScopedRelation[] {
  const objects = new Map<string, SqlRelation | SqlMacro>()
  for (const relation of metadata.relations) {
    objects.set(qualified(relation).toLowerCase(), relation)
  }
  for (const macro of metadata.macros) {
    if (macro.kind === "table_macro") {
      objects.set(qualified(macro).toLowerCase(), macro)
    }
  }

  const found: ScopedRelation[] = []
  const pattern =
    /\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(?:\s*\([^;]*?\))?(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?/gi
  for (const match of input.matchAll(pattern)) {
    const name = match[2]!
    const object = objects.get(`${match[1]}.${name}`.toLowerCase())
    if (!object) continue
    const candidateAlias = match[3]
    const alias =
      candidateAlias && !CLAUSE_WORDS.has(candidateAlias.toLowerCase())
        ? candidateAlias
        : name
    found.push({ alias, name, columns: object.columns })
  }
  return found
}

function addColumns(
  add: (
    value: string,
    kind: Completion["kind"],
    description?: string,
    priority?: number,
  ) => void,
  prefix: string,
  scoped: ScopedRelation[],
): void {
  const dot = prefix.lastIndexOf(".")
  if (dot >= 0) {
    const qualifier = prefix.slice(0, dot).toLowerCase()
    const columnPrefix = prefix.slice(dot + 1)
    for (const relation of scoped.filter(
      (item) => item.alias.toLowerCase() === qualifier,
    )) {
      for (const column of relation.columns) {
        if (!column.name.toLowerCase().startsWith(columnPrefix.toLowerCase())) {
          continue
        }
        add(
          `${relation.alias}.${column.name}`,
          "column",
          columnDescription(relation, column),
          120,
        )
      }
    }
    return
  }

  const counts = new Map<string, number>()
  for (const relation of scoped) {
    for (const column of relation.columns) {
      const key = column.name.toLowerCase()
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
  }
  for (const relation of scoped) {
    for (const column of relation.columns) {
      const duplicate = (counts.get(column.name.toLowerCase()) ?? 0) > 1
      add(
        duplicate ? `${relation.alias}.${column.name}` : column.name,
        "column",
        columnDescription(relation, column),
        100,
      )
    }
  }
}

function qualified(item: { schema_name: string; name: string }): string {
  return `${item.schema_name}.${item.name}`
}

function columnDescription(
  relation: ScopedRelation,
  column: SqlColumn,
): string {
  const shape = `${relation.alias} · ${column.data_type}${
    column.nullable ? " nullable" : ""
  }`
  return column.description ? `${shape} · ${column.description}` : shape
}

function relationDescription(relation: SqlRelation): string {
  const shape = `view · ${relation.columns.length} columns`
  return relation.description ? `${shape} · ${relation.description}` : shape
}

function macroDescription(macro: SqlMacro): string {
  const parameters = macro.parameters
    .map((item) => `${item.name} ${item.data_type}`)
    .join(", ")
  const result =
    macro.kind === "table_macro"
      ? `${macro.columns.length} columns`
      : (macro.return_type ?? "ANY")
  return `${macro.name}(${parameters}) → ${result}`
}
