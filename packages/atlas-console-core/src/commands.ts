import type { CommandResult, Completion } from "./types.js"

export interface CommandDefinition {
  name: string
  summary: string
  execute(): CommandResult
}

export class CommandRegistry {
  private readonly commands: ReadonlyMap<string, CommandDefinition>

  constructor(definitions: readonly CommandDefinition[]) {
    const commands = new Map<string, CommandDefinition>()
    for (const definition of definitions) {
      if (commands.has(definition.name)) {
        throw new Error(`Duplicate command: .${definition.name}`)
      }
      commands.set(definition.name, Object.freeze({ ...definition }))
    }
    this.commands = commands
  }

  all(): readonly CommandDefinition[] {
    return [...this.commands.values()]
  }

  execute(input: string): CommandResult {
    const name = input.slice(1).trim().toLowerCase()
    const command = this.commands.get(name)
    if (!command) throw new Error(`Unknown command: .${name}. Try .help.`)
    return command.execute()
  }

  complete(input: string, cursor = input.length): Completion[] {
    const token = input.slice(1, cursor).trimStart().toLowerCase()
    return this.all()
      .filter((command) => command.name.startsWith(token))
      .map((command) => ({
        value: `.${command.name}`,
        replaceStart: 0,
        replaceEnd: cursor,
        kind: "command" as const,
        description: command.summary,
      }))
  }
}

export const commands: CommandRegistry = new CommandRegistry([
  {
    name: "clear",
    summary: "Clear the screen.",
    execute: () => ({ kind: "clear" }),
  },
  {
    name: "help",
    summary: "Show available commands.",
    execute: (): CommandResult => ({
      kind: "message",
      text: [
        "Commands",
        ...commands
          .all()
          .map((command) => `  .${command.name.padEnd(8)} ${command.summary}`),
        "",
        "Enter read-only SQL against ingest.* or material.*. Press Tab to complete.",
      ].join("\n"),
    }),
  },
])
