import { SqlApi } from "./api.js"
import { commands } from "./commands.js"
import { SqlCompleter } from "./completion.js"
import type { Completion, ConsoleResult, SqlResult } from "./types.js"

export class SqlConsole {
  private readonly completer: SqlCompleter
  private active?: AbortController

  constructor(readonly api: SqlApi) {
    this.completer = new SqlCompleter(() => api.metadata())
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
    if (input.startsWith(".")) return Promise.resolve(commands.complete(input, cursor))
    return this.completer.complete(input, cursor)
  }

  async run(line: string): Promise<ConsoleResult | undefined> {
    const input = line.trim()
    if (!input) return undefined
    if (input.startsWith(".")) return commands.execute(input)
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
}
