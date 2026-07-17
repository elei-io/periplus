import type { CatalogueQueryResult } from "@/types/catalogue"

export const MAX_CRAWL_URLS = 10_000
export const DEFAULT_CRAWL_GRAPH_SLUG = "single-page"

export type CrawlUrlSelection = {
  column: string | null
  urls: string[]
  valueCount: number
  invalidCount: number
  duplicateCount: number
}

export type CrawlColumnCandidate = CrawlUrlSelection & {
  column: string
  columnIndex: number
}

export type CrawlCommandTarget =
  | { kind: "last-result"; graph: string; column: string | null }
  | { kind: "url"; graph: string; url: string }

export function parseCrawlCommandTarget(
  argument: string
): CrawlCommandTarget | { kind: "error"; error: string } {
  const parsed = tokenizeArguments(argument)
  if ("error" in parsed) return parsed

  let graph: string | null = null
  let column: string | null = null
  const positional: string[] = []

  for (let index = 0; index < parsed.tokens.length; index += 1) {
    const token = parsed.tokens[index]
    const option = optionValue(token)
    if (option?.name === "--graph" || token === "--graph") {
      if (graph !== null) {
        return {
          kind: "error",
          error: "Crawl graph may be specified only once.",
        }
      }
      const value =
        option?.value ??
        (parsed.tokens[index + 1]?.startsWith("--")
          ? undefined
          : parsed.tokens[++index])
      if (!value) return usageError()
      graph = value
      continue
    }
    if (option?.name === "--column" || token === "--column") {
      if (column !== null) {
        return {
          kind: "error",
          error: "URL column may be specified only once.",
        }
      }
      const value =
        option?.value ??
        (parsed.tokens[index + 1]?.startsWith("--")
          ? undefined
          : parsed.tokens[++index])
      if (!value) return usageError()
      column = value
      continue
    }
    if (token.startsWith("--")) {
      return {
        kind: "error",
        error: `Unknown crawl option: ${token.split("=", 1)[0]}`,
      }
    }
    positional.push(token)
  }

  if (positional.length > 1 || (column && positional.length > 0)) {
    return usageError()
  }
  graph ??= DEFAULT_CRAWL_GRAPH_SLUG

  return positional.length === 1
    ? { kind: "url", graph, url: positional[0] }
    : { kind: "last-result", graph, column }
}

export function selectionFromUrl(
  value: string
): CrawlUrlSelection | { error: string } {
  const url = httpUrl(value)
  if (!url) return { error: "Crawl URLs must use HTTP or HTTPS." }
  return {
    column: null,
    urls: [url],
    valueCount: 1,
    invalidCount: 0,
    duplicateCount: 0,
  }
}

export function crawlColumnCandidates(
  result: CatalogueQueryResult
): CrawlColumnCandidate[] {
  if (result.statementKind !== "query") return []

  return result.columns.flatMap((column, columnIndex) => {
    const selection = selectionFromColumn(result, columnIndex)
    const nonEmptyCount = result.rows.filter(
      (row) => valueText(row[columnIndex]) !== null
    ).length
    const urlNamed = /(^|_)(url|uri|href|link)($|_)/i.test(column)
    const mostlyUrls =
      nonEmptyCount > 0 &&
      selection.urls.length + selection.duplicateCount >=
        Math.ceil(nonEmptyCount * 0.8)

    return selection.urls.length > 0 && (urlNamed || mostlyUrls)
      ? [{ ...selection, column, columnIndex }]
      : []
  })
}

export function selectionFromNamedColumn(
  result: CatalogueQueryResult,
  requestedColumn: string
): CrawlUrlSelection | { error: string } {
  const matches = result.columns
    .map((column, index) => ({ column, index }))
    .filter(
      ({ column }) =>
        column.toLocaleLowerCase() === requestedColumn.toLocaleLowerCase()
    )

  if (matches.length === 0) {
    return { error: `Column not found in the last result: ${requestedColumn}` }
  }
  if (matches.length > 1) {
    return {
      error: `Column name is ambiguous in the last result: ${requestedColumn}`,
    }
  }

  return selectionFromColumn(result, matches[0].index)
}

function selectionFromColumn(
  result: CatalogueQueryResult,
  columnIndex: number
): CrawlUrlSelection {
  const column = result.columns[columnIndex]
  const values = result.rows
    .map((row) => valueText(row[columnIndex]))
    .filter((value): value is string => value !== null)
  const valid = values
    .map(httpUrl)
    .filter((value): value is string => value !== null)
  const urls = [...new Set(valid)]

  return {
    column,
    urls,
    valueCount: values.length,
    invalidCount: values.length - valid.length,
    duplicateCount: valid.length - urls.length,
  }
}

function valueText(value: unknown) {
  if (typeof value !== "string") return null
  const text = value.trim()
  return text ? text : null
}

function httpUrl(value: string) {
  try {
    const url = new URL(value.trim())
    return url.protocol === "http:" || url.protocol === "https:"
      ? value.trim()
      : null
  } catch {
    return null
  }
}

function usageError() {
  return {
    kind: "error" as const,
    error: "Usage: \\crawl [--graph <graph>] [<url> | --column <column>]",
  }
}

function optionValue(token: string) {
  const match = /^(--(?:graph|column))=(.*)$/.exec(token)
  return match ? { name: match[1], value: match[2] } : null
}

function tokenizeArguments(
  input: string
): { tokens: string[] } | { kind: "error"; error: string } {
  const tokens: string[] = []
  let current = ""
  let quote: "'" | '"' | null = null

  for (let index = 0; index < input.length; index += 1) {
    const character = input[index]
    if (quote) {
      if (character === quote) {
        quote = null
      } else if (character === "\\" && input[index + 1] === quote) {
        current += input[++index]
      } else {
        current += character
      }
      continue
    }

    if (character === "'" || character === '"') {
      quote = character
    } else if (/\s/.test(character)) {
      if (current) {
        tokens.push(current)
        current = ""
      }
    } else {
      current += character
    }
  }

  if (quote) {
    return { kind: "error", error: "Unterminated quote in crawl command." }
  }
  if (current) tokens.push(current)
  return { tokens }
}
