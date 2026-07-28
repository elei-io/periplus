import { SqlApi } from "./api.js"
import { commands } from "./commands.js"
import { SqlCompleter } from "./completion.js"
import type {
  AiEvent,
  AiMessage,
  AiStreamResult,
  Completion,
  ConsoleResult,
  SqlResult,
} from "./types.js"

export class SqlConsole {
  private readonly completer: SqlCompleter
  private active?: AbortController
  private readonly aiHistory: AiMessage[] = []
  readonly history: string[]

  constructor(
    readonly api: SqlApi,
    options: { history?: readonly string[] } = {},
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

  private askAi(prompt: string, fresh: boolean): AiStreamResult {
    this.active?.abort()
    if (fresh) this.aiHistory.length = 0
    const context = this.aiHistory.slice(-6)
    const controller = new AbortController()
    this.active = controller
    const source = this.api.ai(prompt, context, controller.signal)
    const history = this.aiHistory
    const owner = this
    async function* events(): AsyncIterable<AiEvent> {
      try {
        for await (const event of source) {
          if (event.type === "response.completed" && event.response) {
            const draftSummary = event.suggestions
              .map((item) => `${item.title}: ${item.description}`)
              .join("\n")
            const answer = [
              event.response.conclusion,
              ...event.response.evidence,
              event.response.recommendation,
            ]
              .filter(Boolean)
              .join("\n")
            history.push(
              { role: "user", content: prompt },
              {
                role: "assistant",
                content: [answer, draftSummary]
                  .filter(Boolean)
                  .join("\n"),
              },
            )
            if (history.length > 20) history.splice(0, history.length - 20)
          }
          yield event
        }
      } finally {
        if (owner.active === controller) owner.active = undefined
      }
    }
    return { kind: "ai", events: events() }
  }

  private commandContext() {
    return {
      history: this.history,
      metadata: (force = false) => this.completer.metadata(force),
      ai: (prompt: string, fresh: boolean) => this.askAi(prompt, fresh),
    }
  }
}
