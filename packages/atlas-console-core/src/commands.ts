import type {
  AiStreamResult,
  CommandResult,
  Completion,
  SqlMacro,
  SqlMetadata,
  SqlRelation,
} from "./types.js"

export interface CommandContext {
  history: readonly string[]
  metadata(force?: boolean): Promise<SqlMetadata>
  ai(prompt: string, fresh: boolean): AiStreamResult
}

export interface CommandDefinition {
  name: string
  summary: string
  usage: string
  examples: readonly string[]
  execute(args: readonly string[], context: CommandContext): Promise<CommandResult>
  complete?(
    args: readonly string[],
    prefix: string,
    replaceStart: number,
    cursor: number,
    context: CommandContext,
  ): Promise<Completion[]>
}

export class CommandRegistry {
  private readonly commands: ReadonlyMap<string, CommandDefinition>

  constructor(definitions: readonly CommandDefinition[]) {
    const registered = new Map<string, CommandDefinition>()
    for (const definition of definitions) {
      if (registered.has(definition.name)) {
        throw new Error(`Duplicate command: .${definition.name}`)
      }
      registered.set(definition.name, Object.freeze({ ...definition }))
    }
    this.commands = registered
  }

  all(): readonly CommandDefinition[] {
    return [...this.commands.values()]
  }

  async execute(input: string, context: CommandContext): Promise<CommandResult> {
    const tokens = tokenize(input.slice(1))
    const name = tokens.shift()?.toLowerCase() ?? ""
    const command = this.commands.get(name)
    if (!command) throw new Error(`Unknown command: .${name}. Try .help.`)
    return command.execute(tokens, context)
  }

  async complete(
    input: string,
    cursor: number,
    context: CommandContext,
  ): Promise<Completion[]> {
    const beforeCursor = input.slice(1, cursor)
    const commandMatch = beforeCursor.match(/^(\S*)/)
    const commandToken = commandMatch?.[1]?.toLowerCase() ?? ""
    if (!beforeCursor.includes(" ")) {
      return this.all()
        .filter((command) => command.name.startsWith(commandToken))
        .map((command) => ({
          value: `.${command.name}`,
          replaceStart: 0,
          replaceEnd: cursor,
          kind: "command" as const,
          description: command.summary,
        }))
    }

    const command = this.commands.get(commandToken)
    if (!command?.complete) return []
    const argumentSource = beforeCursor.slice(commandToken.length).trimStart()
    const tokens = tokenize(argumentSource)
    const prefix = argumentSource.match(/\S*$/)?.[0] ?? ""
    const completed = prefix ? tokens.slice(0, -1) : tokens
    return command.complete(
      completed,
      prefix,
      cursor - prefix.length,
      cursor,
      context,
    )
  }
}

let commands: CommandRegistry

commands = new CommandRegistry([
  defineLocal("clear", "Clear the screen.", ".clear", () => ({
    kind: "clear",
  })),
  defineLocal("exit", "Exit the console.", ".exit", () => ({ kind: "exit" })),
  {
    name: "ai",
    summary: "Ask Atlas AI about the public catalogue.",
    usage: ".ai [--fresh] <question>",
    examples: [
      ".ai Which pages changed most often?",
      ".ai --fresh Compare response status by hostname",
    ],
    async execute(args, context) {
      const fresh = args[0]?.toLowerCase() === "--fresh"
      const prompt = args.slice(fresh ? 1 : 0).join(" ").trim()
      if (!prompt) throw usageError(".ai [--fresh] <question>")
      return context.ai(prompt, fresh)
    },
  },
  {
    name: "help",
    summary: "Show commands or detailed help.",
    usage: ".help [command]",
    examples: [".help", ".help describe"],
    async execute(args) {
      if (args.length > 1) throw usageError(".help [command]")
      const requested = args[0]?.replace(/^\./, "").toLowerCase()
      if (requested) {
        const command = commands
          .all()
          .find((item) => item.name === requested)
        if (!command) throw new Error(`Unknown command: .${requested}`)
        return {
          kind: "message",
          text: [
            command.usage,
            command.summary,
            "",
            "Examples:",
            ...command.examples.map((example) => `  ${example}`),
          ].join("\n"),
        }
      }
      return {
        kind: "table",
        columns: ["Command", "Description"],
        rows: commands
          .all()
          .map((command) => [command.usage, command.summary]),
        summary:
          "Enter read-only SQL against web.* and dom.*. Press Tab to complete.",
      }
    },
    async complete(_args, prefix, replaceStart, cursor) {
      return commands
        .all()
        .filter((command) => command.name.startsWith(prefix.toLowerCase()))
        .map((command) => ({
          value: command.name,
          replaceStart,
          replaceEnd: cursor,
          kind: "argument" as const,
          description: command.summary,
        }))
    },
  },
  {
    name: "history",
    summary: "Show console input history.",
    usage: ".history",
    examples: [".history"],
    async execute(args, context) {
      if (args.length) throw usageError(".history")
      return {
        kind: "table",
        columns: ["#", "Input"],
        rows: context.history.map((input, index) => [index + 1, input]),
        summary: `${context.history.length} histor${
          context.history.length === 1 ? "y entry" : "y entries"
        }`,
      }
    },
  },
  {
    name: "tables",
    summary: "List public views and table macros.",
    usage: ".tables",
    examples: [".tables"],
    async execute(args, context) {
      if (args.length) throw usageError(".tables")
      const metadata = await context.metadata()
      const rows = [
        ...metadata.relations.map((relation) => [
          qualified(relation),
          "view",
          `${relation.columns.length} columns`,
          relation.description ?? "",
        ]),
        ...metadata.macros
          .filter((macro) => macro.kind === "table_macro")
          .map((macro) => [
            qualified(macro),
            "table macro",
            signature(macro),
            "",
          ]),
      ].sort((left, right) => String(left[0]).localeCompare(String(right[0])))
      return {
        kind: "table",
        columns: ["Name", "Kind", "Signature", "Description"],
        rows,
        summary: `${rows.length} public objects · catalogue ${metadata.catalogue_version}`,
      }
    },
  },
  {
    name: "macros",
    summary: "List public scalar and table macros.",
    usage: ".macros",
    examples: [".macros"],
    async execute(args, context) {
      if (args.length) throw usageError(".macros")
      const metadata = await context.metadata()
      return {
        kind: "table",
        columns: ["Name", "Kind", "Signature"],
        rows: metadata.macros
          .map((macro) => [
            qualified(macro),
            macro.kind === "table_macro" ? "table" : "scalar",
            signature(macro),
          ])
          .sort((left, right) =>
            String(left[0]).localeCompare(String(right[0])),
          ),
        summary: `${metadata.macros.length} public macro${
          metadata.macros.length === 1 ? "" : "s"
        } · catalogue ${metadata.catalogue_version}`,
      }
    },
  },
  {
    name: "describe",
    summary: "Describe a public view or macro.",
    usage: ".describe <object>",
    examples: [".describe web.page", ".describe dom.elements"],
    async execute(args, context) {
      if (args.length !== 1) throw usageError(".describe <object>")
      const metadata = await context.metadata()
      const object = resolveObject(args[0]!, metadata)
      if ("kind" in object && object.kind !== "view") {
        return {
          kind: "table",
          columns: ["Column", "Type", "Nullable"],
          rows: object.columns.map((column) => [
            column.name,
            column.data_type,
            column.nullable ? "yes" : "no",
          ]),
          summary: `${qualified(object)} · ${object.kind.replace("_", " ")} · ${signature(object)}`,
        }
      }
      return {
        kind: "table",
        columns: ["Column", "Type", "Nullable", "Description"],
        rows: object.columns.map((column) => [
          column.name,
          column.data_type,
          column.nullable ? "yes" : "no",
          column.description ?? "",
        ]),
        summary: `${qualified(object)} · view${
          object.description ? ` · ${object.description}` : ""
        }`,
      }
    },
    async complete(_args, prefix, replaceStart, cursor, context) {
      const metadata = await context.metadata()
      return [...metadata.relations, ...metadata.macros]
        .map(qualified)
        .filter((name) => name.toLowerCase().startsWith(prefix.toLowerCase()))
        .map((name) => ({
          value: name,
          replaceStart,
          replaceEnd: cursor,
          kind: "argument" as const,
        }))
    },
  },
  {
    name: "completion",
    summary: "Manage SQL completion metadata.",
    usage: ".completion reload",
    examples: [".completion reload"],
    async execute(args, context) {
      if (args.length !== 1 || args[0]?.toLowerCase() !== "reload") {
        throw usageError(".completion reload")
      }
      const metadata = await context.metadata(true)
      const columns = [
        ...metadata.relations,
        ...metadata.macros.filter((macro) => macro.kind === "table_macro"),
      ].reduce((total, item) => total + item.columns.length, 0)
      return {
        kind: "message",
        text:
          `Completion metadata reloaded: ${metadata.relations.length} views, ` +
          `${metadata.macros.length} macros, ${columns} columns.`,
      }
    },
    async complete(_args, prefix, replaceStart, cursor) {
      return "reload".startsWith(prefix.toLowerCase())
        ? [
            {
              value: "reload",
              replaceStart,
              replaceEnd: cursor,
              kind: "argument",
              description: "Fetch fresh public catalogue metadata.",
            },
          ]
        : []
    },
  },
])

export { commands }

function defineLocal(
  name: string,
  summary: string,
  usage: string,
  execute: () => CommandResult,
): CommandDefinition {
  return {
    name,
    summary,
    usage,
    examples: [usage],
    async execute(args) {
      if (args.length) throw usageError(usage)
      return execute()
    },
  }
}

function tokenize(input: string): string[] {
  const tokens: string[] = []
  let token = ""
  let quote: "'" | '"' | undefined
  for (const character of input.trim()) {
    if (quote) {
      if (character === quote) quote = undefined
      else token += character
    } else if (character === "'" || character === '"') {
      quote = character
    } else if (/\s/.test(character)) {
      if (token) {
        tokens.push(token)
        token = ""
      }
    } else {
      token += character
    }
  }
  if (quote) throw new Error("Unterminated quoted command argument.")
  if (token) tokens.push(token)
  return tokens
}

function resolveObject(
  requested: string,
  metadata: SqlMetadata,
): SqlRelation | SqlMacro {
  const normalized = requested.toLowerCase()
  const matches = [...metadata.relations, ...metadata.macros].filter(
    (item) =>
      item.name.toLowerCase() === normalized ||
      qualified(item).toLowerCase() === normalized,
  )
  if (!matches.length) throw new Error(`Public object not found: ${requested}`)
  if (matches.length > 1) {
    throw new Error(`Public object is ambiguous: ${requested}`)
  }
  return matches[0]!
}

function signature(macro: SqlMacro): string {
  const parameters = macro.parameters
    .map((item) => `${item.name} ${item.data_type}`)
    .join(", ")
  return `${qualified(macro)}(${parameters})${
    macro.return_type ? ` → ${macro.return_type}` : ""
  }`
}

function qualified(item: { schema_name: string; name: string }): string {
  return `${item.schema_name}.${item.name}`
}

function usageError(usage: string): Error {
  return new Error(`Usage: ${usage}`)
}
