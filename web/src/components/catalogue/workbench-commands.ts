import type { CatalogueStatus } from "@/hooks/use-catalogue-status"
import type {
  CatalogueMetadata,
  CatalogueMetadataFunction,
  CatalogueMetadataRelation,
  CatalogueQueryResult,
} from "@/types/catalogue"

export type WorkbenchCommandName =
  | "\\?"
  | "\\d"
  | "\\dt"
  | "\\dv"
  | "\\dm"
  | "\\df"
  | "\\history"
  | "\\x"
  | "\\status"

export type WorkbenchCommand = {
  name: WorkbenchCommandName
  argument: string
}

export type WorkbenchCommandOutcome =
  | { kind: "help" }
  | { kind: "result"; result: CatalogueQueryResult }
  | { kind: "message"; message: string }
  | { kind: "toggle-expanded" }
  | { kind: "error"; error: string }

export const WORKBENCH_COMMANDS = [
  { command: "\\?", description: "Show available meta-commands" },
  { command: "\\d <name>", description: "Describe a table or view" },
  { command: "\\dt [pattern]", description: "List tables" },
  { command: "\\dv [pattern]", description: "List views" },
  { command: "\\dm [pattern]", description: "List table macros" },
  { command: "\\df [pattern]", description: "List functions" },
  { command: "\\history", description: "Show command history" },
  { command: "\\x", description: "Toggle expanded result display" },
  { command: "\\status", description: "Show catalogue and API status" },
  { command: "clear", description: "Clear the transcript" },
] as const

const commandNames = new Set<WorkbenchCommandName>([
  "\\?",
  "\\d",
  "\\dt",
  "\\dv",
  "\\dm",
  "\\df",
  "\\history",
  "\\x",
  "\\status",
])

export function parseWorkbenchCommand(value: string): WorkbenchCommand | null {
  const input = value.trim().replace(/;$/, "").trim()
  if (input.toLocaleLowerCase() === "help") {
    return { name: "\\?", argument: "" }
  }

  const match = /^(\\[?a-z]+)(?:\s+([\s\S]*))?$/i.exec(input)
  if (!match) return null
  const name = match[1].toLocaleLowerCase() as WorkbenchCommandName
  if (!commandNames.has(name)) return null
  return { name, argument: (match[2] ?? "").trim() }
}

export function isWorkbenchCommandLike(value: string) {
  const input = value.trimStart()
  return input.startsWith("\\") || input.startsWith("/")
}

export function workbenchCommandSuggestion(value: string) {
  const input = value.trim().replace(/;$/, "").trim().toLocaleLowerCase()
  if (!input.startsWith("/")) return null
  const suggested = `\\${input.slice(1)}` as WorkbenchCommandName
  return commandNames.has(suggested) ? suggested : null
}

export function runWorkbenchCommand(
  command: WorkbenchCommand,
  context: {
    metadata: CatalogueMetadata | undefined
    status: CatalogueStatus | undefined
    history: string[]
  }
): WorkbenchCommandOutcome {
  switch (command.name) {
    case "\\?":
      return { kind: "help" }
    case "\\x":
      return { kind: "toggle-expanded" }
    case "\\history":
      return {
        kind: "result",
        result: result(
          ["#", "command"],
          ["INTEGER", "VARCHAR"],
          context.history.map((item, index) => [index + 1, item])
        ),
      }
    case "\\status":
      return statusResult(context.status)
    case "\\d":
      return describeRelation(command.argument, context.metadata)
    case "\\dt":
      return relationList("table", command.argument, context.metadata)
    case "\\dv":
      return relationList("view", command.argument, context.metadata)
    case "\\dm":
      return functionList(true, command.argument, context.metadata)
    case "\\df":
      return functionList(false, command.argument, context.metadata)
  }
}

function describeRelation(
  argument: string,
  metadata: CatalogueMetadata | undefined
): WorkbenchCommandOutcome {
  if (!argument) return { kind: "error", error: "Usage: \\d <table-or-view>" }
  if (!metadata) return metadataUnavailable()

  const target = normalizeQualifiedName(argument)
  const matches = metadata.relations.filter((relation) => {
    const names = [
      relation.name,
      `${relation.schema_name}.${relation.name}`,
      `${relation.catalog_name}.${relation.schema_name}.${relation.name}`,
    ]
    return names.some(
      (name) => name.toLocaleLowerCase() === target.toLocaleLowerCase()
    )
  })
  if (matches.length === 0) {
    return { kind: "error", error: `Relation not found: ${argument}` }
  }
  if (matches.length > 1) {
    return {
      kind: "error",
      error: `Relation name is ambiguous: ${argument}. Include its schema.`,
    }
  }

  return {
    kind: "result",
    result: result(
      ["column", "type", "nullable"],
      ["VARCHAR", "VARCHAR", "BOOLEAN"],
      matches[0].columns.map((column) => [
        column.name,
        column.data_type,
        column.nullable,
      ])
    ),
  }
}

function relationList(
  kind: "table" | "view",
  pattern: string,
  metadata: CatalogueMetadata | undefined
): WorkbenchCommandOutcome {
  if (!metadata) return metadataUnavailable()
  const relations = metadata.relations.filter(
    (relation) => kind === relation.kind && matchesPattern(relation, pattern)
  )
  return {
    kind: "result",
    result: result(
      ["schema", "name", "kind"],
      ["VARCHAR", "VARCHAR", "VARCHAR"],
      relations.map((relation) => [
        relation.schema_name,
        relation.name,
        relation.kind,
      ])
    ),
  }
}

function functionList(
  tableMacros: boolean,
  pattern: string,
  metadata: CatalogueMetadata | undefined
): WorkbenchCommandOutcome {
  if (!metadata) return metadataUnavailable()
  const functions = metadata.functions.filter((item) => {
    const matchesKind = tableMacros
      ? item.kind === "table_macro"
      : !item.kind.includes("table")
    return matchesKind && matchesFunctionPattern(item, pattern)
  })
  return {
    kind: "result",
    result: result(
      ["schema", "name", "kind", "parameters", "returns"],
      ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
      functions.map((item) => [
        item.schema_name,
        item.name,
        item.kind,
        formatParameters(item),
        item.return_type,
      ])
    ),
  }
}

function statusResult(
  status: CatalogueStatus | undefined
): WorkbenchCommandOutcome {
  if (!status) {
    return { kind: "error", error: "Catalogue status is still loading." }
  }
  return {
    kind: "result",
    result: result(
      ["property", "value"],
      ["VARCHAR", "VARCHAR"],
      [
        ["active Parquet files", status.active_file_count.toLocaleString()],
        ["catalogue storage", formatBytes(status.active_storage_bytes)],
        ["DuckLake version", status.ducklake_version ?? "unknown"],
        ["Atlas schema", status.catalogue_schema_version.toLocaleString()],
        ["API latency", `${Math.round(status.apiLatencyMs)} ms`],
      ]
    ),
  }
}

function result(
  columns: string[],
  columnTypes: string[],
  rows: unknown[][]
): CatalogueQueryResult {
  return { columns, columnTypes, rows }
}

function metadataUnavailable(): WorkbenchCommandOutcome {
  return { kind: "error", error: "Catalogue metadata is still loading." }
}

function matchesPattern(relation: CatalogueMetadataRelation, pattern: string) {
  return [relation.name, `${relation.schema_name}.${relation.name}`].some(
    (name) => wildcardMatch(name, pattern)
  )
}

function matchesFunctionPattern(
  item: CatalogueMetadataFunction,
  pattern: string
) {
  return [item.name, `${item.schema_name}.${item.name}`].some((name) =>
    wildcardMatch(name, pattern)
  )
}

function wildcardMatch(value: string, pattern: string) {
  if (!pattern) return true
  const normalizedPattern = normalizeQualifiedName(pattern)
  if (!/[?*]/.test(normalizedPattern)) {
    return value
      .toLocaleLowerCase()
      .includes(normalizedPattern.toLocaleLowerCase())
  }
  const expression = normalizedPattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replaceAll("*", ".*")
    .replaceAll("?", ".")
  return new RegExp(`^${expression}$`, "i").test(value)
}

function normalizeQualifiedName(value: string) {
  return value
    .split(".")
    .map((part) => part.trim().replace(/^"|"$/g, ""))
    .join(".")
}

function formatParameters(item: CatalogueMetadataFunction) {
  const parameters = item.parameters.map((parameter) =>
    [parameter.name, parameter.data_type].filter(Boolean).join(" ")
  )
  if (item.varargs) parameters.push(`… ${item.varargs}`)
  return parameters.join(", ")
}

function formatBytes(value: number) {
  if (value < 1_024) return `${value} B`
  const units = ["KB", "MB", "GB", "TB"]
  let size = value / 1_024
  let unit = units[0]
  for (const nextUnit of units.slice(1)) {
    if (size < 1_024) break
    size /= 1_024
    unit = nextUnit
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${unit}`
}
