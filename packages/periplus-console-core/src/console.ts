import { SqlApi } from "./api.js"
import { commands } from "./commands.js"
import { SqlCompleter } from "./completion.js"
import type { Completion, ConsoleResult, SqlResult } from "./types.js"

export class SqlConsole {
  private readonly completer: SqlCompleter
  private active?: AbortController
  readonly history: string[]

  constructor(
    readonly api: SqlApi,
    options: { history?: readonly string[] } = {}
  ) {
    this.completer = new SqlCompleter(() => api.metadata())
    this.history = [...(options.history ?? [])].slice(-100)
  }

  execute(sql: string): Promise<SqlResult> {
    this.active?.abort()
    const controller = new AbortController()
    this.active = controller
    return this.api.query(sql, controller.signal).finally(() => {
      if (this.active === controller) this.active = undefined
    })
  }

  complete(input: string, cursor = input.length): Promise<Completion[]> {
    if (input.startsWith(".")) {
      return commands.complete(input, cursor, this.commandContext())
    }
    return this.completer.complete(input, cursor)
  }

  metadata(force = false) {
    return this.completer.metadata(force)
  }

  async run(line: string): Promise<ConsoleResult | undefined> {
    const input = line.trim()
    if (!input) return undefined
    if (this.history.at(-1) !== input) {
      this.history.push(input)
      if (this.history.length > 100) this.history.shift()
    }
    if (input.startsWith(".")) {
      return commands.execute(input, this.commandContext())
    }
    const startedAt = performance.now()
    const result = await this.execute(input)
    return {
      kind: "query",
      result,
      durationMilliseconds: performance.now() - startedAt,
    }
  }

  interrupt(): boolean {
    if (!this.active) return false
    this.active.abort()
    return true
  }

  private commandContext() {
    return {
      history: this.history,
      metadata: (force = false) => this.completer.metadata(force),
    }
  }
}
