import {
  acceptCompletion,
  autocompletion,
  clearSnippet,
  closeCompletion,
  completionStatus,
  hasNextSnippetField,
  hasPrevSnippetField,
  nextSnippetField,
  prevSnippetField,
  selectedCompletion,
  snippetCompletion,
  startCompletion,
} from "@codemirror/autocomplete"
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from "@codemirror/autocomplete"
import { PostgreSQL, SQLDialect, sql } from "@codemirror/lang-sql"
import { syntaxTree } from "@codemirror/language"
import { Prec } from "@codemirror/state"
import type { Extension } from "@codemirror/state"
import {
  Decoration,
  EditorView,
  keymap,
  ViewPlugin,
  WidgetType,
} from "@codemirror/view"
import type { DecorationSet, ViewUpdate } from "@codemirror/view"

import type {
  CatalogueMetadata,
  CatalogueMetadataFunction,
  CatalogueMetadataRelation,
} from "@/types/catalogue"

const DuckDBDialect = SQLDialect.define({
  ...PostgreSQL.spec,
  caseInsensitiveIdentifiers: true,
})

const terminalCompletions: Completion[] = [
  { label: "\\?", type: "keyword", detail: "command · show help" },
  { label: "\\d", type: "keyword", detail: "command · describe relation" },
  {
    label: "\\dt",
    type: "keyword",
    detail: "command · list tables",
  },
  { label: "\\dv", type: "keyword", detail: "command · list views" },
  { label: "\\dm", type: "keyword", detail: "command · list table macros" },
  { label: "\\df", type: "keyword", detail: "command · list functions" },
  { label: "\\history", type: "keyword", detail: "command · show history" },
  { label: "\\x", type: "keyword", detail: "command · expanded display" },
  { label: "\\status", type: "keyword", detail: "command · show status" },
  { label: "help", type: "keyword", detail: "command · show help" },
  { label: "clear", type: "keyword", detail: "command · clear transcript" },
]

const atlasFunctionCompletions = [
  snippetCompletion("css_select('${selector}')", {
    label: "css_select",
    displayLabel: "css_select('…')",
    type: "function",
    detail: "function · inferred elements source",
    info: "Matches elements with a CSS selector.",
    boost: 20,
  }),
  snippetCompletion("get_attribute('${attribute}')", {
    label: "get_attribute",
    displayLabel: "get_attribute('…')",
    type: "function",
    detail: "function · inferred element",
    boost: 20,
  }),
  snippetCompletion("has_attribute('${attribute}')", {
    label: "has_attribute",
    displayLabel: "has_attribute('…')",
    type: "function",
    detail: "function · inferred element",
    boost: 20,
  }),
  ...["readable_text", "text_content", "inner_html"].map((name) =>
    snippetCompletion(`${name}()`, {
      label: name,
      displayLabel: `${name}()`,
      type: "function",
      detail: "function · inferred element",
      boost: 20,
    })
  ),
  snippetCompletion("resolve_url(${source}, ${href})", {
    label: "resolve_url",
    displayLabel: "resolve_url(…, …)",
    type: "function",
    detail: "function · Atlas scalar",
    boost: 15,
  }),
]

const sqlSyntaxCompletions = [
  snippetCompletion("coalesce(${value}, ${fallback})", {
    label: "coalesce",
    displayLabel: "coalesce(…, …)",
    type: "function",
    detail: "SQL expression",
    boost: 4,
  }),
  snippetCompletion("nullif(${left}, ${right})", {
    label: "nullif",
    displayLabel: "nullif(…, …)",
    type: "function",
    detail: "SQL expression",
    boost: 4,
  }),
  snippetCompletion("cast(${value} AS ${type})", {
    label: "cast",
    displayLabel: "cast(… AS …)",
    type: "function",
    detail: "SQL expression",
    boost: 4,
  }),
  snippetCompletion("try_cast(${value} AS ${type})", {
    label: "try_cast",
    displayLabel: "try_cast(… AS …)",
    type: "function",
    detail: "DuckDB expression",
    boost: 4,
  }),
]

const clauseKeywords: Record<string, string[]> = {
  START: ["SELECT", "WITH", "EXPLAIN"],
  SELECT: ["AS", "DISTINCT", "FROM"],
  FROM: ["WHERE", "JOIN", "LEFT JOIN", "GROUP BY", "ORDER BY", "LIMIT"],
  JOIN: ["ON", "USING"],
  WHERE: [
    "AND",
    "OR",
    "IS NULL",
    "IN",
    "LIKE",
    "GROUP BY",
    "ORDER BY",
    "LIMIT",
  ],
  ON: ["AND", "OR", "WHERE", "GROUP BY", "ORDER BY", "LIMIT"],
  HAVING: ["AND", "OR", "ORDER BY", "LIMIT"],
  GROUP: ["HAVING", "ORDER BY", "LIMIT"],
  ORDER: ["ASC", "DESC", "NULLS FIRST", "NULLS LAST", "LIMIT"],
}

const clausePattern =
  /\b(SELECT|FROM|JOIN|WHERE|ON|HAVING|GROUP|ORDER|LIMIT|UPDATE|INTO)\b/gi
const relationStartPattern = /\b(FROM|JOIN|UPDATE|INTO)\b/gi
const identifierPart = String.raw`(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)`
const relationReferencePattern = new RegExp(
  String.raw`\b(?:FROM|JOIN)\s+(${identifierPart}(?:\s*\.\s*${identifierPart}){0,2})(?:\s+(?:AS\s+)?(${identifierPart}))?`,
  "gi"
)
const macroReferenceStartPattern = new RegExp(
  String.raw`\b(?:FROM|JOIN)\s+(${identifierPart}(?:\s*\.\s*${identifierPart}){0,2})\s*\(`,
  "gi"
)
const aliasAfterCallPattern = new RegExp(
  String.raw`^\s+(?:AS\s+)?(${identifierPart})`,
  "i"
)
const aliasStopWords = new Set([
  "AND",
  "CROSS",
  "FULL",
  "GROUP",
  "HAVING",
  "INNER",
  "JOIN",
  "LEFT",
  "LIMIT",
  "ON",
  "ORDER",
  "OUTER",
  "RIGHT",
  "UNION",
  "USING",
  "WHERE",
])

export function createCatalogueCompletionExtensions(
  metadata: CatalogueMetadata | undefined
): Extension[] {
  const language = sql({ dialect: DuckDBDialect })
  const source = createCatalogueCompletionSource(metadata)

  return [
    language,
    autocompletion({
      activateOnTyping: true,
      activateOnTypingDelay: 150,
      defaultKeymap: false,
      icons: false,
      maxRenderedOptions: 8,
      override: [source],
      selectOnOpen: true,
      tooltipClass: () => "atlas-terminal-completions",
    }),
    activateCompletionOnEdit,
    ghostTextPlugin,
    completionKeys,
  ]
}

export function createCatalogueCompletionSource(
  metadata: CatalogueMetadata | undefined
): CompletionSource {
  return (context) => {
    const terminal = completeTerminalCommand(context, metadata)
    if (terminal) return terminal
    if (!isSqlCode(context)) return null

    const word = context.matchBefore(/[A-Za-z_][\w$]*$/)
    const prefix = word?.text ?? ""
    const before = context.state.sliceDoc(0, context.pos)
    const qualifier = qualifierBefore(before, word?.from ?? context.pos)
    if (prefix.length === 0 && !qualifier) return null
    let options: Completion[]

    if (isRelationPosition(before, word?.from ?? context.pos)) {
      options = relationCompletions(metadata, qualifier)
    } else if (qualifier) {
      options = qualifiedCompletions(
        context.state.doc.toString(),
        metadata,
        qualifier
      )
    } else {
      const keywords = keywordCompletions(before)
      options = isStatementStart(before, word?.from ?? context.pos)
        ? keywords
        : [
            ...keywords,
            ...scopeColumnCompletions(context.state.doc.toString(), metadata),
            ...functionCompletions(metadata),
          ]
    }

    const matching = uniqueCompletions(options).filter((option) =>
      option.label.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())
    )
    if (matching.length === 0) return null

    return {
      from: word?.from ?? context.pos,
      options: matching.slice(0, 1),
      filter: false,
      validFor: /^[\w$]*$/,
    }
  }
}

function isStatementStart(before: string, wordFrom: number) {
  const beforeWord = maskSql(before.slice(0, wordFrom))
  const currentStatement = beforeWord.slice(beforeWord.lastIndexOf(";") + 1)
  return currentStatement.trim() === ""
}

function isSqlCode(context: CompletionContext) {
  let node = syntaxTree(context.state).resolveInner(context.pos, -1)
  for (;;) {
    if (/String|Comment/.test(node.name)) return false
    if (!node.parent) return true
    node = node.parent
  }
}

function qualifierBefore(before: string, wordFrom: number) {
  const match = /(?:^|[^\w$])([A-Za-z_][\w$]*)\s*\.\s*$/.exec(
    before.slice(0, wordFrom)
  )
  return match?.[1]
}

function isRelationPosition(before: string, wordFrom: number) {
  const masked = maskSql(before.slice(0, wordFrom))
  let last: RegExpExecArray | null = null
  relationStartPattern.lastIndex = 0
  for (
    let match = relationStartPattern.exec(masked);
    match;
    match = relationStartPattern.exec(masked)
  ) {
    last = match
  }
  if (!last) return false

  const after = masked.slice(last.index + last[0].length)
  const segment = after.slice(after.lastIndexOf(",") + 1)
  if (segment.trim() === "") return true

  // We are still completing the relation token until whitespace starts an alias.
  return new RegExp(
    String.raw`^\s*${identifierPart}(?:\s*\.\s*${identifierPart}){0,2}\s*\.\s*$`
  ).test(segment)
}

function relationCompletions(
  metadata: CatalogueMetadata | undefined,
  qualifier: string | undefined
) {
  if (!metadata) return []
  const normalizedQualifier = qualifier?.toLocaleLowerCase()
  const options: Completion[] = []

  for (const relation of metadata.relations) {
    if (normalizedQualifier) {
      if (relation.schema_name.toLocaleLowerCase() !== normalizedQualifier)
        continue
    } else if (relation.schema_name !== metadata.default_schema) {
      continue
    }
    options.push(relationCompletion(relation))
  }

  for (const item of metadata.functions) {
    if (!item.kind.includes("table")) continue
    if (normalizedQualifier) {
      if (item.schema_name.toLocaleLowerCase() !== normalizedQualifier) continue
    } else if (item.schema_name !== metadata.default_schema) {
      continue
    }
    options.push(functionCompletion(item, item.name))
  }

  if (!qualifier) options.push(...schemaCompletions(metadata))
  return options
}

function relationCompletion(relation: CatalogueMetadataRelation): Completion {
  return {
    label: relation.name,
    type: relation.kind === "view" ? "interface" : "class",
    detail: `${relation.kind} · ${relation.schema_name}`,
    boost: relation.kind === "view" ? 9 : 10,
  }
}

function schemaCompletions(metadata: CatalogueMetadata) {
  return [
    ...new Set(metadata.relations.map((relation) => relation.schema_name)),
  ].map((schema): Completion => ({
    label: schema,
    type: "namespace",
    detail: "schema",
  }))
}

function qualifiedCompletions(
  sqlText: string,
  metadata: CatalogueMetadata | undefined,
  qualifier: string
) {
  if (!metadata) return []
  const normalized = qualifier.toLocaleLowerCase()
  const scope = referencedRelations(sqlText, metadata)
  const relation = scope.get(normalized)
  if (relation) return columnCompletions(relation, qualifier)

  return [
    ...relationCompletions(metadata, qualifier),
    ...metadata.functions
      .filter((item) => item.schema_name.toLocaleLowerCase() === normalized)
      .map((item) => functionCompletion(item, item.name)),
  ]
}

function scopeColumnCompletions(
  sqlText: string,
  metadata: CatalogueMetadata | undefined
) {
  if (!metadata) return []
  const options: Completion[] = []
  const seenRelations = new Set<CatalogueMetadataRelation>()
  for (const [alias, relation] of referencedRelations(sqlText, metadata)) {
    if (seenRelations.has(relation)) continue
    seenRelations.add(relation)
    options.push(...columnCompletions(relation, alias))
  }
  return options
}

function columnCompletions(
  relation: CatalogueMetadataRelation,
  source: string
): Completion[] {
  return relation.columns.map((column) => ({
    label: column.name,
    type: "property",
    detail: `${column.data_type}${column.nullable ? "?" : ""} · ${source}`,
    boost: 20,
  }))
}

function referencedRelations(sqlText: string, metadata: CatalogueMetadata) {
  const relations = new Map<string, CatalogueMetadataRelation>()
  const masked = maskSql(sqlText)
  relationReferencePattern.lastIndex = 0
  for (
    let match = relationReferencePattern.exec(masked);
    match;
    match = relationReferencePattern.exec(masked)
  ) {
    const parts = match[1].split(".").map(normalizeIdentifier)
    const name = parts.at(-1)
    const schema = parts.length > 1 ? parts.at(-2) : metadata.default_schema
    const relation = metadata.relations.find(
      (item) =>
        item.name.toLocaleLowerCase() === name?.toLocaleLowerCase() &&
        item.schema_name.toLocaleLowerCase() === schema?.toLocaleLowerCase()
    )
    if (!relation) continue

    const possibleAlias = match[2] ? normalizeIdentifier(match[2]) : undefined
    const alias =
      possibleAlias && !aliasStopWords.has(possibleAlias.toLocaleUpperCase())
        ? possibleAlias
        : relation.name
    relations.set(alias.toLocaleLowerCase(), relation)
    relations.set(relation.name.toLocaleLowerCase(), relation)
  }

  macroReferenceStartPattern.lastIndex = 0
  for (
    let match = macroReferenceStartPattern.exec(masked);
    match;
    match = macroReferenceStartPattern.exec(masked)
  ) {
    const openingParenthesis = macroReferenceStartPattern.lastIndex - 1
    const closingParenthesis = matchingParenthesis(masked, openingParenthesis)
    if (closingParenthesis === null) continue

    const parts = match[1].split(".").map(normalizeIdentifier)
    const name = parts.at(-1)
    const schema = parts.length > 1 ? parts.at(-2) : metadata.default_schema
    const macro = metadata.functions.find(
      (item) =>
        item.kind === "table_macro" &&
        item.name.toLocaleLowerCase() === name?.toLocaleLowerCase() &&
        item.schema_name.toLocaleLowerCase() === schema?.toLocaleLowerCase()
    )
    if (!macro || macro.result_columns.length === 0) continue

    const possibleAlias = aliasAfterCallPattern.exec(
      masked.slice(closingParenthesis + 1)
    )?.[1]
    const normalizedAlias = possibleAlias
      ? normalizeIdentifier(possibleAlias)
      : undefined
    const alias =
      normalizedAlias &&
      !aliasStopWords.has(normalizedAlias.toLocaleUpperCase())
        ? normalizedAlias
        : macro.name
    const relation: CatalogueMetadataRelation = {
      catalog_name: macro.catalog_name,
      schema_name: macro.schema_name,
      name: macro.name,
      kind: "table",
      columns: macro.result_columns,
    }
    relations.set(alias.toLocaleLowerCase(), relation)
    relations.set(macro.name.toLocaleLowerCase(), relation)
  }
  return relations
}

function matchingParenthesis(value: string, openingIndex: number) {
  let depth = 0
  for (let index = openingIndex; index < value.length; index += 1) {
    if (value[index] === "(") depth += 1
    if (value[index] !== ")") continue
    depth -= 1
    if (depth === 0) return index
  }
  return null
}

function normalizeIdentifier(value: string) {
  const normalized = value.trim()
  return normalized.startsWith('"')
    ? normalized.slice(1, -1).replaceAll('""', '"')
    : normalized
}

function functionCompletions(metadata: CatalogueMetadata | undefined) {
  const completions = new Map<string, Completion>()
  for (const item of atlasFunctionCompletions) completions.set(item.label, item)
  for (const item of sqlSyntaxCompletions) completions.set(item.label, item)
  if (!metadata) return [...completions.values()]

  for (const item of metadata.functions) {
    if (item.schema_name !== metadata.default_schema) continue
    if (!completions.has(item.name)) {
      completions.set(item.name, functionCompletion(item, item.name))
    }
  }
  return [...completions.values()]
}

function functionCompletion(
  item: CatalogueMetadataFunction,
  label: string
): Completion {
  const parameters = item.parameters.map((parameter) => parameter.name)
  if (item.varargs) parameters.push("values")
  const snippet = `${label}(${parameters
    .map((parameter) => `\${${parameter}}`)
    .join(", ")})`
  return snippetCompletion(snippet, {
    label,
    displayLabel: `${label}(${parameters.length > 0 ? "…" : ""})`,
    type: item.kind.includes("table") ? "class" : "function",
    detail: [item.kind.replace("_", " "), item.return_type, item.schema_name]
      .filter(Boolean)
      .join(" · "),
    info: item.description ?? functionSignature(label, item),
    boost: item.kind.includes("macro") ? 10 : 4,
  })
}

function functionSignature(
  label: string,
  item: CatalogueMetadataFunction
): string {
  const parameters = item.parameters.map((parameter) =>
    [parameter.name, parameter.data_type].filter(Boolean).join(" ")
  )
  if (item.varargs) parameters.push(`… ${item.varargs}`)
  return `${label}(${parameters.join(", ")})${item.return_type ? ` → ${item.return_type}` : ""}`
}

function keywordCompletions(before: string): Completion[] {
  const maskedDocument = maskSql(before)
  const masked = maskedDocument.slice(maskedDocument.lastIndexOf(";") + 1)
  let clause = "START"
  clausePattern.lastIndex = 0
  for (
    let match = clausePattern.exec(masked);
    match;
    match = clausePattern.exec(masked)
  ) {
    clause = match[1].toLocaleUpperCase()
  }
  return (clauseKeywords[clause] ?? []).map((label) => ({
    label,
    type: "keyword",
    detail: "keyword",
    boost: -5,
  }))
}

function maskSql(value: string) {
  return value
    .replace(/--[^\n]*/g, (match) => " ".repeat(match.length))
    .replace(/\/\*[\s\S]*?\*\//g, (match) => " ".repeat(match.length))
    .replace(/'(?:''|[^'])*'/g, (match) => " ".repeat(match.length))
}

function uniqueCompletions(options: Completion[]) {
  const seen = new Set<string>()
  return options.filter((option) => {
    const key = `${option.label}\u0000${option.detail ?? ""}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function completeTerminalCommand(
  context: CompletionContext,
  metadata: CatalogueMetadata | undefined
): CompletionResult | null {
  const line = context.state.doc.lineAt(context.pos)
  const before = context.state.sliceDoc(line.from, context.pos)
  const describeMatch = /^\s*\\d\s+([\w$.]+)$/i.exec(before)
  if (describeMatch && metadata) {
    const prefix = describeMatch[1]
    const options = metadata.relations
      .map((relation): Completion => ({
        label:
          relation.schema_name === metadata.default_schema
            ? relation.name
            : `${relation.schema_name}.${relation.name}`,
        type: relation.kind === "view" ? "interface" : "class",
        detail: `${relation.kind} · ${relation.schema_name}`,
      }))
      .filter((option) =>
        option.label.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())
      )
    if (options.length === 0) return null
    return {
      from: context.pos - prefix.length,
      options: options.slice(0, 1),
      filter: false,
      validFor: /^[\w$.]*$/,
    }
  }

  const match = /^\s*(\\[\w?]*|(?:cl|he)\w*)$/i.exec(before)
  if (!match) return null

  const options = terminalCompletions.filter((option) =>
    option.label.toLocaleLowerCase().startsWith(match[1].toLocaleLowerCase())
  )
  if (options.length === 0) return null
  return {
    from: context.pos - match[1].length,
    options,
    filter: false,
    validFor: match[1].startsWith("\\") ? /^\\[\w?]*$/ : /^(?:cl|he)\w*$/i,
  }
}

const completionKeys = Prec.highest(
  keymap.of([
    {
      key: "Tab",
      run: (view) => {
        if (hasNextSnippetField(view.state)) return nextSnippetField(view)
        const status = completionStatus(view.state)
        if (status === "active") return acceptCompletion(view)
        if (status === "pending") return true
        return startCompletion(view)
      },
    },
    {
      key: "Shift-Tab",
      run: (view) => {
        if (hasPrevSnippetField(view.state)) return prevSnippetField(view)
        return startCompletion(view)
      },
    },
    { key: "Ctrl-Space", run: startCompletion },
    { key: "Mod-Space", run: startCompletion },
    {
      key: "Escape",
      run: (view) => closeCompletion(view) || clearSnippet(view),
    },
  ])
)

const activateCompletionOnEdit = EditorView.updateListener.of((update) => {
  if (!update.docChanged || !update.state.selection.main.empty) return
  startCompletion(update.view)
})

class GhostTextWidget extends WidgetType {
  readonly text: string

  constructor(text: string) {
    super()
    this.text = text
  }

  eq(other: GhostTextWidget) {
    return this.text === other.text
  }

  toDOM() {
    const element = document.createElement("span")
    element.className = "cm-atlasGhostText"
    element.setAttribute("aria-hidden", "true")
    element.textContent = this.text
    return element
  }

  ignoreEvent() {
    return true
  }
}

function ghostTextDecorations(view: EditorView): DecorationSet {
  if (completionStatus(view.state) !== "active") return Decoration.none
  const completion = selectedCompletion(view.state)
  const selection = view.state.selection.main
  if (!completion || !selection.empty) return Decoration.none

  const before = view.state.sliceDoc(0, selection.head)
  const terminalPrefix =
    /^\s*(?:\\d\s+([\w$.]+)|(\\[\w?]+|(?:cl|he)\w+))$/i.exec(before)
  const commandPrefix = terminalPrefix?.[1] ?? terminalPrefix?.[2]
  const prefix = commandPrefix ?? /[A-Za-z_][\w$]*$/.exec(before)?.[0] ?? ""
  const qualifier = qualifierBefore(before, selection.head - prefix.length)
  if (!prefix && !qualifier) return Decoration.none
  const display = completion.displayLabel ?? completion.label
  if (!display.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())) {
    return Decoration.none
  }

  let suffix = display.slice(prefix.length)
  if (prefix && prefix === prefix.toLocaleLowerCase())
    suffix = suffix.toLocaleLowerCase()
  if (!suffix) return Decoration.none

  return Decoration.set([
    Decoration.widget({
      widget: new GhostTextWidget(suffix),
      side: 1,
    }).range(selection.head),
  ])
}

const ghostTextPlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet

    constructor(view: EditorView) {
      this.decorations = ghostTextDecorations(view)
    }

    update(update: ViewUpdate) {
      this.decorations = ghostTextDecorations(update.view)
    }
  },
  { decorations: (plugin) => plugin.decorations }
)
